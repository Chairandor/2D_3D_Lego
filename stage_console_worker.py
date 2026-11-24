"""Run one console action with the exact inputs selected when it was clicked."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

import pipeline_2d_to_lego as pipeline
from stage_console import ACTIONS, COLOR, FOREGROUND, PLY, RECORD, SHAPE, SHEET, UPRIGHT
from stage_console import identity, metadata, save_outputs
from staged_lego import atomic_json, read_preview


def publish(work: Path, run: Path, files: list[str]) -> None:
    for relative in files:
        if not (work / relative).is_file():
            raise RuntimeError(f"未生成预期产物：{relative}")
    for relative in files:
        final = run / relative
        final.parent.mkdir(parents=True, exist_ok=True)
        (work / relative).replace(final)


def outputs(work: Path, directory: str, names: tuple[str, ...] | None = None) -> list[str]:
    folder = work / directory
    candidates = folder.glob("**/*") if names is None else (folder / name for name in names)
    return sorted(p.relative_to(work).as_posix() for p in candidates if p.is_file())


def execute(plan_path: Path) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    run = Path(plan["run"])
    work = plan_path.parent
    action = plan["action"]
    inputs = {key: Path(value) for key, value in plan["inputs"].items()}
    if {key: identity(file) for key, file in inputs.items() if key != "cache_run"} != plan["input_versions"]:
        raise ValueError("运行之前输入版本已变化，请重新点击阶段按钮")

    print(f"阶段操作：{ACTIONS[action]}", flush=True)
    if action == "foreground":
        pipeline.prepare_foreground_auto(inputs["source"], work / "input")
        publish(work, run, outputs(work, "input"))
        save_outputs(run, pipeline.ROOT, {FOREGROUND: {}}, inputs)
        pipeline.record_pipeline_state(run, "foreground_review", foreground=run / FOREGROUND)
        return

    if action == "geometry":
        ref = inputs[FOREGROUND]
        original = Path(plan["image"]) if plan.get("image") else None
        reference_is_original = original is not None and ref == original
        if reference_is_original:
            ref, _ = pipeline.accept_transparent_input(
                original, work / "input", pipeline.inspect_transparent_input(original),
            )
        raw = pipeline.reconstruct_hunyuan(ref, work / "hunyuan", steps=50, seed=42,
                                           octree_resolution=384, cpu_offload=False)
        pipeline.clean_and_review_shape(raw, ref, work / "hunyuan")
        publish(work, run, outputs(work, "hunyuan"))
        if reference_is_original:
            publish(work, run, outputs(work, "input"))
            save_outputs(run, pipeline.ROOT, {FOREGROUND: {}}, {"source": original})
            ref = run / FOREGROUND
        save_outputs(run, pipeline.ROOT, {SHAPE: {}}, {FOREGROUND: ref})
        pipeline.record_pipeline_state(run, "geometry_review", foreground=ref, shape=run / SHAPE)
        return

    if action in {"views", "texture"}:
        sheet = inputs.get(SHEET)
        pipeline.run_unitex_remote(
            inputs[FOREGROUND], inputs[SHAPE], work / "unitex",
            run_dir=work, host=pipeline.DEFAULT_UNITEX_HOST, key=pipeline.DEFAULT_UNITEX_KEY,
            remote_root=pipeline.DEFAULT_UNITEX_ROOT, steps=28, seed=63,
            projection=plan["projection"] if sheet else "ltm",
            gpu_index=int(os.environ.get("UNITEX_GPU", "1")),
            phase="project" if sheet else "stage1", force=True,
            six_view_override=sheet, cache_run_dir=inputs.get("cache_run"),
            require_stage1_cache=bool(sheet) and not plan.get("allow_cache_rebuild"),
            cache_projection="ltm",
        )
        if action == "views":
            publish(work, run, outputs(work, "unitex", (
                "mv_rgb.png", "mv_alpha.png", "mv_normal.png", "mv_ccm.png",
                "processed_image.png", "rembg_image.png", "mv_rgb_w_light.png",
            )))
            # The newly generated sheet defines a new editable silhouette.
            previous_mask = run / "ui_revisions/six_view_reference"
            if previous_mask.is_dir():
                previous_mask.rename(work / "previous_six_view_reference")
            save_outputs(run, pipeline.ROOT, {SHEET: {"cache_run": str(work)}}, inputs)
            pipeline.record_pipeline_state(run, "unitex_review", foreground=inputs[FOREGROUND],
                                           shape=inputs[SHAPE], six_view=run / SHEET)
        else:
            pipeline.convert_via_lego_python(work / COLOR, work / PLY, orient="auto", colour="lit",
                                             lego_palette="common", colour_coherence=True, refine=False)
            publish(work, run, outputs(work, "unitex", (
                "textured_mesh.glb", "completed_uv.png", "valid_uv_mask.png", "visable_uv_mask.png",
            )) + outputs(work, "oriented"))
            save_outputs(run, pipeline.ROOT, {COLOR: {"projection": plan["projection"]}}, inputs)
            save_outputs(run, pipeline.ROOT, {PLY: {}, UPRIGHT: {}}, {COLOR: run / COLOR})
            pipeline.record_pipeline_state(run, "oriented_review", textured=run / COLOR, lego_input=run / PLY)
        return

    if action == "lego":
        ply = inputs.get("ply")
        if ply is None:
            ply = work / PLY
            pipeline.convert_via_lego_python(inputs["glb"], ply, orient="auto", colour="lit",
                                             lego_palette="common", colour_coherence=True, refine=False)
        studs = plan["studs"]
        lego_run = Path(plan["lego_run"]) if plan.get("lego_run") else run
        folder = lego_run / f"lego-{studs}"
        print(f"[5/5] legolization preview studs={studs}", flush=True)
        pipeline.run_legolization_preview(ply, folder, studs, plan["bundle"])
        marker = read_preview(folder)
        if marker is None:
            raise RuntimeError("缺少 LEGO 排砖预览记录")
        items = metadata(lego_run)
        items[f"lego-{studs}/model.mpd"] = {"product": marker["token"], "inputs": plan["input_versions"]}
        atomic_json(lego_run / RECORD, items)
        pipeline.record_pipeline_state(run, "lego_preview", target_studs=studs, lego_model=None)
        return
    raise ValueError("无效的阶段操作")


if __name__ == "__main__":
    try:
        execute(Path(sys.argv[1]))
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
