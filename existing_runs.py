"""Import known teammate run artifacts for preview and downstream 3D editing."""

from __future__ import annotations

import json
import hashlib
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
    "fox-reviewed": (
        Path("/data/data_zxn/3D/model/Fox"),
        "狐狸 Fox（已验收的 Prox-E 去脚结果）",
    ),
}

REVIEWED_EXAMPLES = {
    "fox-reviewed": {
        "source": Path("/data/data_zxn/2D_3D_Lego_server/runs/imported-textured_mesh-20261025-222650-417110/oriented/mesh_upright.glb"),
        "result": Path("/data/data_zxn/2D_3D_Lego_server/runs/imported-textured_mesh-20261025-222650-417110/ai_edit/edit-20261026-021851-123912/proxe_output/fox/mesh_upright/output_calibrated.glb"),
        "source_relative": "unitex/textured_mesh.glb",
        "instruction": "去掉狐狸的四只脚",
    },
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
            if identifier in REVIEWED_EXAMPLES and relative.startswith("lego/"):
                continue
            origin = source / relative
            if origin.is_file() and not origin.is_symlink() and origin.resolve().is_relative_to(source.resolve()):
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, output)
        for origin in (() if identifier in REVIEWED_EXAMPLES else source.glob("lego-*/model.mpd")):
            if origin.is_file() and not origin.is_symlink() and origin.resolve().is_relative_to(source.resolve()):
                output = target / origin.relative_to(source)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, output)
        if identifier in REVIEWED_EXAMPLES:
            reviewed = REVIEWED_EXAMPLES[identifier]
            model = target / reviewed["source_relative"]
            with model.open("rb") as current, reviewed["source"].open("rb") as original:
                if hashlib.file_digest(current, "sha256").digest() != hashlib.file_digest(original, "sha256").digest():
                    raise ValueError("已验收结果与当前贴图模型不属于同一份输入")
            result = target / "ai_edit/reviewed/output_calibrated.glb"
            result.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(reviewed["result"], result)
            (target / "ai_edit_state.json").write_text(json.dumps({
                "status": "review",
                "latest_result": str(result),
                "latest_instruction": reviewed["instruction"],
                "history": [{"source": str(model), "output": str(result),
                             "instruction": reviewed["instruction"]}],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        state_file = source / "ai_edit_state.json"
        if identifier not in REVIEWED_EXAMPLES and state_file.is_file() and not state_file.is_symlink():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            reviewed = Path(str(state.get("latest_result", ""))).resolve()
            if reviewed.is_file() and reviewed.is_relative_to(source.resolve()):
                relative = reviewed.relative_to(source.resolve())
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(reviewed, output)
                (target / "ai_edit_state.json").write_text(json.dumps({
                    "status": "review",
                    "latest_result": str(output),
                    "latest_instruction": state.get("latest_instruction", ""),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(target)
        raise
    return target
