"""Persist a native Legolization layout before running its physics pass."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4


MARKER = ".preview-state.json"


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_preview(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / MARKER).read_text(encoding="utf-8"))
        if (data.get("version") == 1 and data.get("state") in {"pending", "verified", "unbuildable"}
                and isinstance(data.get("brick_count"), int)
                and re.fullmatch(r"[a-f0-9]{32}", str(data.get("token", "")))):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return None


def draft_folder(folder: Path, state: dict) -> Path:
    drafts = folder / "drafts" / state["token"]
    if not drafts.resolve().is_relative_to(folder.resolve()):
        raise ValueError("排砖预览记录指向了成果目录之外")
    return drafts


def pending_preview(folder: Path) -> tuple[dict, Path]:
    state = read_preview(folder)
    if not state or state["state"] != "pending":
        raise ValueError("这张卡片没有待验证的排砖预览")
    draft = draft_folder(folder, state)
    files = ("preview.mpd", ".checkpoint/layout.json", ".checkpoint/grid.npz")
    if any(not (draft / name).is_file() or not (draft / name).resolve().is_relative_to(draft.resolve())
           for name in files):
        raise ValueError("排砖预览的中间文件不完整，请重新生成预览")
    return state, draft


def place(args: argparse.Namespace) -> int:
    import numpy as np
    from legolization.catalog import default_catalog
    from legolization.cli.build import build_configuration
    from legolization.ldraw_out import write_model
    from legolization.pipeline import Deadline, _prepare_grid, _strategy, load_grid

    if args.source is None:
        raise ValueError("缺少 LEGO 输入网格")
    config = build_configuration(args).to_pipeline(strategy="fast")
    source = load_grid(args.source, config)
    working = _prepare_grid(source, config)
    if not working.filled_count:
        raise ValueError("体素化没有得到可铺砖的格子")
    catalog = default_catalog()
    rng = np.random.default_rng(config.seed)
    print("LEGO 预览：正在排第一版砖", flush=True)
    layout = _strategy(catalog, config).place(
        working, rng=rng, deadline=Deadline.after(config.time_budget_s),
    )
    if not layout.bricks:
        raise ValueError("排砖没有返回可显示的布局")
    token = uuid4().hex
    draft = draft_folder(args.folder, {"token": token})
    checkpoint = draft / ".checkpoint"
    checkpoint.mkdir(parents=True)
    with (checkpoint / "grid.npz").open("wb") as stream:
        np.savez_compressed(stream, source=source.codes, working=working.codes)
    atomic_json(checkpoint / "layout.json", {
        "bricks": [asdict(brick) for brick in layout], "next_id": layout._next_id,
        "rng": rng.bit_generator.state, "overrides": args.set_overrides,
        "mesh_features": asdict(source.mesh_features) if source.mesh_features else None,
    })
    if args.bundle:
        shutil.copyfile(args.source, checkpoint / "lego_input.ply")
    write_model(layout, draft / "preview.mpd")
    atomic_json(args.folder / MARKER, {
        "version": 1, "token": token, "state": "pending",
        "brick_count": len(layout), "bundle": args.bundle,
    })
    print(f"LEGO 预览已生成，{len(layout)} 件零件，等待验证", flush=True)
    return 0


def restore_layout(data: dict, catalog):
    from legolization.layout import Layout

    layout = Layout(catalog)
    for row in data["bricks"]:
        if row["brick_id"] < layout._next_id:
            raise ValueError("排砖预览的砖块编号无效")
        layout._next_id = row["brick_id"]
        layout.add(row["part_key"], row["x"], row["y"], row["layer"], row["yaw"],
                   row["colour_code"], offset_ldu=tuple(row["offset_ldu"]))
    layout._next_id = max(layout._next_id, data["next_id"])
    return layout


def verify(args: argparse.Namespace) -> int:
    import numpy as np
    from legolization.catalog import default_catalog
    from legolization.cli.build import build_configuration
    from legolization.grid import MeshFeatureAnnotations, VoxelGrid
    from legolization.pipeline import (
        Deadline, _complete_pipeline, _PipelineState, _PlacementPhase, _remerge, write_outputs,
    )
    from legolization.placement.repair import repair_stability
    from legolization.stability.solver import analyze

    state, draft = pending_preview(args.folder)
    checkpoint = draft / ".checkpoint"
    saved = json.loads((checkpoint / "layout.json").read_text(encoding="utf-8"))
    args.set_overrides = saved["overrides"]
    config = build_configuration(args).to_pipeline(strategy="fast")
    if config.hollow:
        raise ValueError("分阶段验证要求原始实体体素")
    features = saved["mesh_features"]
    if features is not None:
        features["grid_phase"] = tuple(features["grid_phase"])
        features["detail_candidates"] = tuple(map(tuple, features["detail_candidates"]))
        features["local_normals"] = tuple((tuple(cell), tuple(normal)) for cell, normal in features["local_normals"])
        features = MeshFeatureAnnotations(**features)
    with np.load(checkpoint / "grid.npz", allow_pickle=False) as data:
        source = VoxelGrid(data["source"], mesh_features=features)
        working = VoxelGrid(data["working"], mesh_features=features)
    catalog = default_catalog()
    layout = restore_layout(saved, catalog)
    rng = np.random.default_rng(config.seed)
    rng.bit_generator.state = saved["rng"]
    deadline = Deadline.after(config.time_budget_s)
    print("LEGO 验证：正在检查已保存的第一版布局", flush=True)
    stability = analyze(layout, config.solver)
    if config.repair and not stability.stable:
        print("LEGO 验证：正在尝试局部重排", flush=True)
        repair = repair_stability(
            layout, working, catalog=catalog, solver_config=config.solver,
            rng=rng, config=config.repair_config, deadline=deadline, initial_stability=stability,
        )
        stability = repair.result if repair.result is not None else analyze(layout, config.solver)
    pipeline = _PipelineState(
        source=source, working=working, config=config, catalog=catalog, rng=rng,
        deadline=deadline, placement=_PlacementPhase(layout, stability),
    )
    _remerge(pipeline)
    result = _complete_pipeline(pipeline)
    # Use the real basename while generating the MPD's internal submodel names.
    staging = checkpoint / "model.mpd"
    write_outputs(result, staging, progress=config.progress)
    staging.replace(args.folder / "model.mpd")
    atomic_json(args.folder / MARKER, {
        **state, "state": "verified" if result.buildable else "unbuildable",
        "brick_count": result.brick_count, "stable": result.stability.stable,
        "components": result.component_count, "floating": result.floating_count,
    })
    print(f"LEGO 验证结束，{result.brick_count} 件零件，"
          f"{'通过' if result.buildable else '未通过'}", flush=True)
    return 0 if result.buildable else 2


def main() -> int:
    from legolization.cli.common import add_config_options

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("place", "verify"))
    parser.add_argument("folder", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--bundle", action="store_true")
    add_config_options(parser)
    args = parser.parse_args()
    return place(args) if args.mode == "place" else verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
