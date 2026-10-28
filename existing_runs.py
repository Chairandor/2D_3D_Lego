"""Import known teammate run artifacts for preview and downstream 3D editing."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

ARTIFACTS = (
    "input/foreground.png", "input/review.png",
    "hunyuan/qa/shape_qa_overlay.png", "hunyuan/mesh_shape_review_clean.glb",
    "unitex/mv_rgb.png", "unitex/textured_mesh.glb",
    "oriented/mesh_upright.glb", "oriented/lego_input.ply",
    "lego/model.mpd",
)

LOCAL_EXAMPLES = {
    "fox-demo": (Path("/data/data_zxn/3D/model/Fox"), "狐狸 Fox（完整流程示例）"),
}


def source_root() -> Path:
    return Path(os.environ.get("EXAMPLE_RUNS_ROOT", "/data/data_hyt/2D_3D_Lego/runs")).resolve()


def list_runs() -> list[dict[str, object]]:
    root = source_root()
    runs = []
    for identifier, (path, label) in LOCAL_EXAMPLES.items():
        if (path / "oriented/mesh_upright.glb").is_file():
            runs.append({"id": identifier, "label": label, "model": "mesh_upright.glb"})
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_dir() or path.is_symlink() or path.parent.resolve() != root:
                continue
            model = path / "oriented/mesh_upright.glb"
            if not model.is_file():
                model = path / "unitex/textured_mesh.glb"
            if model.is_file():
                runs.append({"id": path.name, "label": path.name, "model": model.name})
    return sorted(runs, key=lambda item: str(item["id"]), reverse=True)


def copy_run(identifier: str, destination_root: Path) -> Path:
    if identifier not in {str(item["id"]) for item in list_runs()}:
        raise ValueError("找不到这个已有结果")
    source = LOCAL_EXAMPLES[identifier][0] if identifier in LOCAL_EXAMPLES else source_root() / identifier
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    target = destination_root / "runs" / f"imported-example-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    try:
        for relative in ARTIFACTS:
            origin = source / relative
            if origin.is_file() and not origin.is_symlink() and origin.resolve().is_relative_to(source.resolve()):
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, output)
        for origin in source.glob("lego-*/model.mpd"):
            if origin.is_file() and not origin.is_symlink() and origin.resolve().is_relative_to(source.resolve()):
                output = target / origin.relative_to(source)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, output)
    except Exception:
        shutil.rmtree(target)
        raise
    return target
