"""Read existing pipeline results within the folder the user opened."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path


IMAGE_ARTIFACTS = (
    "input/foreground.png", "input/review.png",
    "hunyuan/mesh_shape_review_clean.glb", "unitex/mv_rgb.png",
    "unitex/textured_mesh.glb", "oriented/mesh_upright.glb", "oriented/lego_input.ply",
    "lego/model.mpd",
)
FLOWS_3D = {"stl", "ply_plain", "ply_color"}
PREVIEWS = {".glb", ".ply", ".mpd", ".ldr"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
ARTIFACTS = PREVIEWS | IMAGES | {".stl", ".obj", ".mtl", ".pdf", ".html", ".npz", ".npy", ".csv", ".zip"}
IMPORT_RECORD = "ui_imported_from.json"
PATH_RECORDS = {
    "ui_current_state.json", "pipeline_state.json", "ui_stage_dependencies.json", "ui_branch.json",
    "state.json", "preprocess.json", "reference.json", "ai_edit_state.json",
}
RESULT_DIRS = {"input", "hunyuan", "unitex", "oriented", "lego", "ui_revisions", "ai_edit", "ui_assistant", ".stage-work", ".ui-details"}


def inside(path: Path, folder: Path) -> Path | None:
    try:
        resolved = path.resolve()
        return resolved if resolved.is_relative_to(folder.resolve()) else None
    except (OSError, RuntimeError, ValueError):
        return None


def read_json(path: Path, folder: Path) -> dict:
    source = inside(path, folder)
    if source is None or not source.is_file():
        return {}
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"成果记录格式无效：{source}")
    return value


def folder_type(folder: Path) -> str:
    if not folder.is_dir():
        raise ValueError(f"找不到成果文件夹：{folder}。请填写运行此 UI 的机器上的目录。")
    if (folder / "ui_branch.json").is_file():
        raise ValueError("请选择成果主文件夹。ui_revisions 中的分支会自动恢复。")
    if (folder / "pipeline3d.json").is_file():
        flow = read_json(folder / "pipeline3d.json", folder).get("flow")
        if flow not in FLOWS_3D:
            raise ValueError("三维成果的 pipeline3d.json 缺少有效的流程类型")
        return "3d"
    markers = ("ui_current_state.json", "pipeline_state.json", *IMAGE_ARTIFACTS)
    if any((target := inside(folder / entry, folder)) and target.is_file() for entry in markers):
        return "image"
    if any(path.is_file() and inside(path, folder) for path in folder.glob("lego-[0-9]*/model.mpd")):
        return "image"
    raise ValueError("未识别到流水线成果。请选择包含 pipeline_state.json、pipeline3d.json 或阶段产物的主文件夹。")


def resolve_folder(folder: Path) -> tuple[Path, str]:
    try:
        return folder, folder_type(folder)
    except ValueError:
        wrapped = folder / folder.name
        if wrapped.is_dir() and not wrapped.is_symlink() and not (folder / "ui_branch.json").exists():
            return wrapped, folder_type(wrapped)
        raise


def parse_folder(value: str) -> Path:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    if not value:
        raise ValueError("请填写成果主文件夹路径")
    return Path(value).expanduser().resolve()


def recorded_path(value: object, folder: Path, old_folder: str = "") -> Path | None:
    """Resolve legacy absolute references after a complete folder was moved."""
    if not isinstance(value, str) or not value:
        return None
    portable = value.replace("\\", "/")
    if portable.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", portable):
        stored = Path(value)
        if stored.is_absolute():
            direct = inside(stored, folder)
            if direct is not None:
                return direct
        old = old_folder.replace("\\", "/").rstrip("/")
        if not old or not portable.startswith(old + "/"):
            return None
        portable = portable[len(old) + 1:]
    if ".." in portable.split("/"):
        return None
    return inside(folder / portable, folder)


def branch_parent(branch: Path, folder: Path) -> Path | None:
    """A saved branch always lives under its parent's ui_revisions/branches."""
    if branch.parent.name != "branches" or branch.parent.parent.name != "ui_revisions":
        return None
    parent = inside(branch.parent.parent.parent, folder)
    if parent is not None and read_json(branch / "ui_branch.json", folder).get("parent_run_dir"):
        return parent
    return None


def legacy_origin(folder: Path) -> str:
    state = read_json(folder / "pipeline_state.json", folder)
    for key, suffix in (
        ("foreground", "input/foreground.png"),
        ("textured", "unitex/textured_mesh.glb"),
        ("lego_input", "oriented/lego_input.ply"),
    ):
        value = state.get(key)
        if isinstance(value, str):
            value = value.replace("\\", "/")
            if value.endswith("/" + suffix):
                return value[:-(len(suffix) + 1)]
    revisions = read_json(folder / "ui_revisions/state.json", folder)
    active = revisions.get("active_run_dir")
    if isinstance(active, str):
        active = active.replace("\\", "/")
        if "/ui_revisions/branches/" in active:
            return active.split("/ui_revisions/branches/", 1)[0]
    return ""


def imported_from(folder: Path) -> str:
    value = read_json(folder / IMPORT_RECORD, folder).get("source_folder")
    return value if isinstance(value, str) else ""


def _restore_snapshot_choices(copy: Path, target: Path) -> None:
    import ui_revisions

    snapshot = read_json(copy / "ui_current_state.json", copy)

    def copied_path(value):
        if not isinstance(value, str):
            return None
        path = Path(value)
        if path.is_absolute():
            if not path.is_relative_to(target):
                return None
            path = path.relative_to(target)
        return inside(copy / path, copy)

    active = copied_path(snapshot.get("active_run"))
    if active is None or not active.is_dir():
        return
    state_path = active / "ui_revisions/state.json"
    state = read_json(state_path, copy) or {"version": 1, "pairs": {}}
    pairs = state["pairs"]
    added = False
    for group in snapshot.get("artifact_groups", []):
        relative, choice = group.get("relative", ""), group.get("choice")
        if relative in pairs or not ui_revisions.stage_for(relative) or group.get("ai_edit"):
            continue
        if choice not in {"original", "candidate"}:
            continue
        original = copied_path((group.get("original") or {}).get("file"))
        candidate = copied_path((group.get("candidate") or {}).get("file"))
        if original is None or candidate is None or not original.is_file() or not candidate.is_file():
            continue
        pairs[relative] = {
            "original": str(target / original.relative_to(copy)),
            "candidate": str(target / candidate.relative_to(copy)),
            "choice": choice, "origin": "restored", "candidate_mtime_ns": candidate.stat().st_mtime_ns,
        }
        added = True
    if added:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def import_image_folder(folder: Path, project: Path) -> Path:
    """Make a writable run from an external one, preserving paths and mtimes."""
    folder = folder.resolve()
    base = project / "runs" / "imported"
    base.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(folder).encode()).hexdigest()[:12]
    name = re.sub(r"[^\w.-]+", "-", folder.name).strip(".-")[:64] or "run"
    target = base / f"{name}-{digest}"
    if target.is_dir():
        if imported_from(target) != str(folder):
            raise ValueError(f"导入目录已被占用：{target}")
        return target

    roots = [str(folder)]
    if old := legacy_origin(folder):
        roots.append(old)
    pointers = read_json(folder / "ui_revisions/state.json", folder).get("active_run_dir")
    if isinstance(pointers, str):
        roots.append(pointers.replace("\\", "/").split("/ui_revisions/branches/", 1)[0])
    for marker in (folder / "ui_revisions/branches").glob("*/ui_branch.json"):
        value = read_json(marker, folder).get("parent_run_dir")
        if isinstance(value, str):
            roots.append(value)
    prefixes = sorted({p.replace("\\", "/").rstrip("/") for p in roots if p.strip("/\\")}, key=len, reverse=True)

    def rewrite(data):
        if isinstance(data, dict):
            return {rewrite(key): rewrite(value) for key, value in data.items()}
        if isinstance(data, list):
            return [rewrite(value) for value in data]
        if isinstance(data, str):
            portable = data.replace("\\", "/")
            for prefix in prefixes:
                value, old = (portable.casefold(), prefix.casefold()) if re.match(r"^[A-Za-z]:", prefix) else (portable, prefix)
                if value == old or value.startswith(old + "/"):
                    suffix = portable[len(prefix):].lstrip("/")
                    if ".." not in suffix.split("/"):
                        return str(target / suffix)
        return data

    def ignore(directory, names):
        return [name for name in names if (Path(directory) / name).is_symlink()
                or name.endswith((".lock", ".tmp", ".pem", ".key", ".pfx"))
                or (Path(directory).name == "unitex" and name == "cache")
                or name in {".git", ".env", "config.local.json", "local.json", IMPORT_RECORD}]

    with tempfile.TemporaryDirectory(prefix=".import-", dir=base) as temporary:
        copy = Path(temporary) / "run"
        copy.mkdir()
        for entry in folder.iterdir():
            if entry.name in ignore(folder, [entry.name]):
                continue
            destination = copy / entry.name
            if entry.is_dir() and (entry.name in RESULT_DIRS or re.fullmatch(r"lego-\d+(?:-bundle(?:-\d+)?)?", entry.name)):
                shutil.copytree(entry, destination, ignore=ignore)
            elif entry.is_file() and (entry.name in PATH_RECORDS or entry.suffix.lower() in IMAGES):
                shutil.copy2(entry, destination)
        for path in copy.rglob("*.json"):
            if path.name not in PATH_RECORDS or (path.name == "state.json" and path.parent.name != "ui_revisions"):
                continue
            data = read_json(path, copy)
            updated = rewrite(data)
            if updated != data:
                path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        _restore_snapshot_choices(copy, target)
        (copy / IMPORT_RECORD).write_text(json.dumps({"source_folder": str(folder)}, ensure_ascii=False), encoding="utf-8")
        copy.rename(target)
    return target


def downloadable(path: Path, folder: Path) -> bool:
    target = inside(path, folder)
    if target is None or not target.is_file():
        return False
    relative = target.relative_to(folder.resolve())
    if any(part.startswith(".") and part != ".ui-details" for part in relative.parts):
        return False
    parts = relative.parts
    while len(parts) > 3 and parts[:2] == ("ui_revisions", "branches"):
        parts = parts[3:]
    if len(parts) == 1:
        return parts[0] in {"voxel_preview.json", "source_occupancy.npz"}
    if target.name in {"config.local.json", "local.json"}:
        return False
    section = parts[0]
    if not (
        section in {"input", "hunyuan", "unitex", "oriented", "lego", "ai_edit",
                    "source", "mesh", "pointcloud", "texture", "voxel", "solver", "assembly", "ui_assistant"}
        or re.fullmatch(r"lego-\d+(?:-bundle(?:-\d+)?)?", section)
        or parts[:2] == ("ui_revisions", "assets")
    ):
        return False
    return target.suffix.lower() in ARTIFACTS or target.name in {
        "bom.json", "audit.json", "bundle.json", "report.json",
    }


def _native_origin(state: dict) -> str:
    # The preserved source asset lives under the run even if the uploaded input
    # and the original platform no longer exist.
    for asset in state["assets"]:
        if isinstance(asset, dict) and asset.get("id") == "original" and isinstance(asset.get("path"), str):
            return asset["path"].replace("\\", "/").partition("/source/original.")[0]
    return ""


def native_assets(folder: Path, state: dict) -> list[tuple[dict, Path]]:
    assets = state.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("三维成果记录里的产物列表格式无效")
    origin = _native_origin(state) if assets else ""
    rows = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        path = recorded_path(asset.get("path"), folder, origin)
        if path is not None and downloadable(path, folder):
            rows[path] = (asset, path)
    report = state.get("report", {})
    summary = report.get("legolization", {}) if isinstance(report, dict) else {}
    if isinstance(summary, dict):
        bundle = recorded_path(summary.get("bundle_path"), folder, origin)
        audit = recorded_path(summary.get("instruction_audit_path"), folder, origin)
        model = recorded_path(summary.get("model_path"), folder, origin)
        for path, key, label in (
            (bundle, "bundle", "LEGO Bundle"),
            (audit, "audit", "装配审计报告"),
            (model, "lego", "LEGO 模型"),
            (bundle.parent / "bom/bom.json" if bundle else None, "bom", "LEGO 零件清单"),
        ):
            if path is not None and downloadable(path, folder):
                rows.setdefault(path, ({"id": key, "label": label, "stage": "assembly"}, path))
    return list(rows.values())


def native_view(folder: Path, ldraw_available: bool) -> dict:
    state = read_json(folder / "pipeline3d.json", folder)
    groups = []
    for asset, path in native_assets(folder, state):
        suffix = path.suffix.lower()
        relative = path.relative_to(folder).as_posix()
        stage = "lego" if suffix in {".mpd", ".ldr"} else "native"
        if suffix in IMAGES:
            kind = "image"
        elif suffix in PREVIEWS:
            kind = "model"
        else:
            kind = "file"
        item = {
            "label": str(asset.get("label") or path.name), "relative": relative, "kind": kind,
            "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
            "url": "/file?" + urllib.parse.urlencode({"path": str(path)}),
        }
        if suffix in PREVIEWS and (stage != "lego" or ldraw_available):
            item["preview_format"] = suffix[1:]
        groups.append({"relative": relative, "stage": stage, "original": item,
                       "candidate": None, "choice": None, "stale": False})
    return {"artifact_groups": groups, "pipeline_state": {
        "pipeline": "3d", "flow": state.get("flow"), "status": state.get("status"),
        "stages": state.get("stages", []),
        "stage": "complete" if state.get("status") == "complete" else "",
    }}
