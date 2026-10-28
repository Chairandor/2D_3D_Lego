"""Small, dependency-free bridge between the pipeline UI and Prox-E.

The heavy Prox-E modules are deliberately never imported here.  The UI can
therefore still start on Windows or on a Linux host without Prox-E; an edit is
launched as a separate, optional subprocess only after the user requests one.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path


STATE_NAME = "ai_edit_state.json"


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_state(run_dir: Path | None) -> dict[str, object]:
    return _read_json(run_dir / STATE_NAME) if run_dir else {}


def write_state(run_dir: Path, state: dict[str, object]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / STATE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def safe_run_file(run_dir: Path, value: object) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser().resolve()
    try:
        path.relative_to(run_dir.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def original_model(run_dir: Path | None) -> Path | None:
    if run_dir is None:
        return None
    for relative in ("unitex/textured_mesh.glb", "oriented/mesh_upright.glb"):
        candidate = run_dir / relative
        if candidate.is_file():
            return candidate.resolve()
    return None


def current_model(run_dir: Path | None) -> Path | None:
    if run_dir is None:
        return None
    state = read_state(run_dir)
    selected = safe_run_file(run_dir, state.get("selected_model"))
    source = safe_run_file(run_dir, state.get("source_model"))
    return selected or source or original_model(run_dir)


def selected_lego_input(run_dir: Path | None) -> Path | None:
    if run_dir is None:
        return None
    return safe_run_file(run_dir, read_state(run_dir).get("selected_lego_input"))


def installation(project_root: Path) -> dict[str, object]:
    default_root = project_root.parent / "3D" / "third_party" / "Prox-E"
    root_text = os.environ.get("PROXE_ROOT", "").strip()
    root = Path(root_text).expanduser() if root_text else default_root
    env_script = Path(
        os.environ.get("PROXE_ENV_SCRIPT", str(root / "scripts" / "proxe_env.sh"))
    ).expanduser()
    relay_config = Path(
        os.environ.get("PROXE_RELAY_CONFIG", str(root / "config" / "proxe_relay.env"))
    ).expanduser()
    inference = root / "inference.py"
    missing = [
        label
        for label, path in (
            ("Prox-E", root),
            ("Prox-E 环境脚本", env_script),
            ("中转站配置", relay_config),
            ("Prox-E 推理入口", inference),
        )
        if not path.exists()
    ]
    return {
        "available": not missing,
        "root": root.resolve() if root.exists() else root,
        "env_script": env_script.resolve() if env_script.exists() else env_script,
        "relay_config": relay_config.resolve() if relay_config.exists() else relay_config,
        "inference": inference.resolve() if inference.exists() else inference,
        "missing": missing,
    }


def clean_category(value: object) -> str:
    text = str(value or "object").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-")
    return text[:48] or "object"


def create_edit_folder(run_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    folder = run_dir / "ai_edit" / f"edit-{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def _prox_shell(install: dict[str, object], args: list[str], gpu: int) -> tuple[list[str], Path]:
    shell = " && ".join(
        (
            "set -e",
            f"source {shlex.quote(str(install['env_script']))}",
            f"source {shlex.quote(str(install['relay_config']))}",
            f"export CUDA_VISIBLE_DEVICES={gpu}",
            "exec " + shlex.join(args),
        )
    )
    return ["bash", "-lc", shell], Path(install["root"])


def build_plan_command(
    project_root: Path, *, source: Path, instruction: str,
    plan_path: Path, category: str, gpu: int,
) -> tuple[list[str], Path]:
    install = installation(project_root)
    if not install["available"]:
        raise ValueError("Prox-E 尚未配置：" + "、".join(install["missing"]))
    if not source.is_file():
        raise ValueError(f"找不到当前 3D 模型：{source}")
    args = [
        "python", "scripts/plan_edit.py", "--input_mesh", str(source),
        "--instruction", instruction, "--output", str(plan_path),
    ]
    if category.strip():
        args.extend(("--category", clean_category(category)))
    return _prox_shell(install, args, gpu)


def build_compose_command(
    project_root: Path, *, source: Path, sample: Path, plan_path: Path, gpu: int,
) -> tuple[list[str], Path]:
    install = installation(project_root)
    args = [
        "python", "scripts/compose_addition.py", "--source", str(source),
        "--sample", str(sample), "--plan", str(plan_path),
    ]
    return _prox_shell(install, args, gpu)


def build_preserve_appearance_command(
    project_root: Path, *, source: Path, generated: Path,
    sample: Path, output: Path, gpu: int,
) -> tuple[list[str], Path]:
    install = installation(project_root)
    args = [
        "python", "scripts/preserve_appearance.py",
        "--source", str(source), "--generated", str(generated),
        "--sample", str(sample), "--output", str(output),
    ]
    return _prox_shell(install, args, gpu)


def build_command(
    project_root: Path,
    *,
    source: Path,
    output_folder: Path,
    instruction: str,
    category: str,
    gpu: int,
    edit_plan_path: Path | None = None,
    stop_after_proxy: bool = False,
) -> tuple[list[str], Path]:
    install = installation(project_root)
    if not install["available"]:
        raise ValueError("Prox-E 尚未配置：" + "、".join(install["missing"]))
    if gpu < 0:
        raise ValueError("GPU 编号不能为负数")
    if not source.is_file():
        raise ValueError(f"找不到当前 3D 模型：{source}")
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("请输入希望如何修改 3D 模型")
    if len(instruction) > 2000:
        raise ValueError("修改要求不能超过 2000 个字符")

    args = [
        "python",
        "inference.py",
        "--input_mesh",
        str(source),
        "--category",
        clean_category(category),
        "--output_folder",
        str(output_folder),
        "--vlm",
        "gpt",
        "--appearance_edit_model",
        "relay",
        # Prox-E parses whether the instruction changes appearance. Let its
        # original-image path preserve texture for geometry-only edits; the
        # image model is still used when the prompt explicitly changes looks.
        "--inject_original",
        "--edit_instruction",
        instruction,
        "--max_iterations",
        "1" if stop_after_proxy else "2",
    ]
    if edit_plan_path is not None:
        args.extend(("--edit_plan_path", str(edit_plan_path)))
    if stop_after_proxy:
        args.append("--stop_after_proxy")
    return _prox_shell(install, args, gpu)


def validate_mesh(path: Path) -> dict[str, int | bool]:
    """Validate a GLB after welding UV-split vertices by position."""
    import numpy as np
    import trimesh

    loaded = trimesh.load(path, process=False)
    meshes = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
    meshes = [mesh for mesh in meshes if isinstance(mesh, trimesh.Trimesh)]
    if not meshes:
        raise ValueError("输出 GLB 中没有可用网格")
    mesh = trimesh.util.concatenate(meshes)
    rounded = np.round(np.asarray(mesh.vertices), decimals=6)
    _, inverse = np.unique(rounded, axis=0, return_inverse=True)
    faces = inverse[np.asarray(mesh.faces)]
    faces = faces[
        (faces[:, 0] != faces[:, 1])
        & (faces[:, 1] != faces[:, 2])
        & (faces[:, 2] != faces[:, 0])
    ]
    edges = np.sort(
        np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])),
        axis=1,
    )
    _, counts = np.unique(edges, axis=0, return_counts=True)
    metrics = {
        "vertices": int(np.unique(inverse).size),
        "faces": int(faces.shape[0]),
        "boundary_edges": int(np.count_nonzero(counts == 1)),
        "nonmanifold_edges": int(np.count_nonzero(counts > 2)),
    }
    metrics["watertight"] = not metrics["boundary_edges"] and not metrics["nonmanifold_edges"]
    if not metrics["watertight"]:
        raise ValueError(
            "最终网格验收未通过："
            f"边界边 {metrics['boundary_edges']}，非流形边 {metrics['nonmanifold_edges']}"
        )
    return metrics


def _texture_profile(path: Path) -> dict[str, object]:
    """Summarize the dominant colored texels without assuming a shared UV atlas."""
    import numpy as np
    import trimesh

    loaded = trimesh.load(path, process=False)
    meshes = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
    images = []
    vertex_colors = []
    for mesh in meshes:
        material = getattr(mesh.visual, "material", None)
        image = getattr(material, "baseColorTexture", None)
        if image is None:
            image = getattr(material, "image", None)
        if image is not None:
            images.append(image)
        elif getattr(mesh.visual, "kind", None) == "vertex":
            vertex_colors.append(np.asarray(mesh.visual.vertex_colors, dtype=np.float32).reshape(-1, 4))
    if images:
        image = max(images, key=lambda item: item.width * item.height).copy()
        image.thumbnail((384, 384))
        pixels = np.asarray(image.convert("RGBA"), dtype=np.float32).reshape(-1, 4)
    elif vertex_colors:
        pixels = np.concatenate(vertex_colors)
    else:
        raise ValueError(f"模型没有可检查的贴图或顶点色：{path.name}")
    rgb = pixels[:, :3]
    maximum = rgb.max(axis=1)
    minimum = rgb.min(axis=1)
    saturation = (maximum - minimum) / np.maximum(maximum, 1)
    valid = (pixels[:, 3] > 64) & (maximum > 30)
    colorful = valid & (saturation > 0.25)
    chosen = colorful if colorful.sum() >= max(100, valid.sum() // 20) else valid
    if not chosen.any():
        raise ValueError(f"模型贴图没有可比较的有效像素：{path.name}")
    selected = rgb[chosen]
    return {
        "sampled_pixels": int(chosen.sum()),
        "median_rgb": np.median(selected, axis=0).round(1).tolist(),
        "median_brightness": float(np.median(selected.max(axis=1))),
        "median_saturation": float(np.median(saturation[chosen])),
    }


def validate_appearance_preservation(source: Path, output: Path) -> dict[str, object]:
    """Reject obvious whole-object recoloring when the instruction asked to keep it."""
    original = _texture_profile(source)
    generated = _texture_profile(output)
    ratio = generated["median_brightness"] / max(original["median_brightness"], 1)
    saturation_shift = abs(generated["median_saturation"] - original["median_saturation"])
    metrics = {
        "source": original,
        "output": generated,
        "brightness_ratio": round(ratio, 3),
        "saturation_shift": round(saturation_shift, 3),
        "passed": 0.82 <= ratio <= 1.22 and saturation_shift <= 0.18,
    }
    if not metrics["passed"]:
        raise ValueError(
            "原有外观明显漂移，已拒绝采用："
            f"贴图亮度比例 {ratio:.2f}、饱和度差 {saturation_shift:.2f}"
        )
    return metrics


def validate_exact_source_preservation(source: Path, output: Path) -> dict[str, int | bool]:
    """For add-only edits, require every source mesh and UV texture to survive unchanged."""
    import numpy as np
    import trimesh

    original = trimesh.load(source, process=False)
    edited = trimesh.load(output, process=False)
    old_meshes = list(original.geometry.values()) if isinstance(original, trimesh.Scene) else [original]
    new_meshes = list(edited.geometry.values()) if isinstance(edited, trimesh.Scene) else [edited]
    if len(new_meshes) <= len(old_meshes):
        raise ValueError("新增路线没有产生独立的新网格")

    def same_mesh(a: object, b: object) -> bool:
        if not (np.array_equal(a.vertices, b.vertices) and np.array_equal(a.faces, b.faces)):
            return False
        if not np.array_equal(getattr(a.visual, "uv", None), getattr(b.visual, "uv", None)):
            return False
        a_mat = getattr(a.visual, "material", None)
        b_mat = getattr(b.visual, "material", None)
        a_img = getattr(a_mat, "baseColorTexture", None)
        b_img = getattr(b_mat, "baseColorTexture", None)
        if a_img is None or b_img is None:
            return a_img is None and b_img is None
        return np.array_equal(np.asarray(a_img.convert("RGBA")), np.asarray(b_img.convert("RGBA")))

    remaining = new_meshes.copy()
    for old in old_meshes:
        match_index = next((index for index, new in enumerate(remaining) if same_mesh(old, new)), None)
        if match_index is None:
            raise ValueError("新增路线改变了原模型的几何或贴图，已拒绝采用")
        remaining.pop(match_index)
    return {
        "source_meshes_preserved": len(old_meshes),
        "added_meshes": len(remaining),
        "original_geometry_and_texture_identical": True,
    }


def validate_exact_source_removal(source: Path, output: Path) -> dict[str, int | bool]:
    """Check the composed mesh still uses the source's exact texture pixels."""
    import numpy as np
    import trimesh

    original = trimesh.load(source, force="scene", process=False).dump(concatenate=False)
    result = trimesh.load(output, force="scene", process=False).dump(concatenate=False)
    if len(original) != 1 or len(result) < 1:
        raise ValueError("精确保留贴图的删除路线需要单个原始网格")
    image = getattr(getattr(original[0].visual, "material", None), "baseColorTexture", None)
    if image is None:
        raise ValueError("原模型没有可核对的贴图")
    matches = [
        mesh for mesh in result
        if (other := getattr(getattr(mesh.visual, "material", None), "baseColorTexture", None)) is not None
        and np.array_equal(np.asarray(image.convert("RGBA")), np.asarray(other.convert("RGBA")))
    ]
    if len(matches) != 1 or len(matches[0].faces) >= len(original[0].faces):
        raise ValueError("删除结果未保留原贴图，或没有删除目标部件")
    return {
        "source_texture_pixels_identical": True,
        "preserved_faces": len(matches[0].faces),
        "removed_faces": len(original[0].faces) - len(matches[0].faces),
    }


def find_output(edit_folder: Path) -> Path | None:
    candidates = sorted(
        edit_folder.rglob("output.glb"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    return candidates[0].resolve() if candidates else None


def output_preview(output: Path) -> Path | None:
    candidate = output.with_suffix(".png")
    return candidate.resolve() if candidate.is_file() else None
