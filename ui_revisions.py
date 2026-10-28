"""Non-destructive artifact alternatives for the local pipeline UI."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path


REVISION_DIR = "ui_revisions"
BRANCH_FILE = "ui_branch.json"
_STATE_THREAD_LOCK = threading.RLock()

EDITABLE_ARTIFACTS: dict[str, tuple[str, str]] = {
    "input/foreground.png": ("input", ".png"),
    "hunyuan/mesh_shape_review_clean.glb": ("geometry", ".glb"),
    "unitex/mv_rgb.png": ("texture", ".png"),
    "unitex/textured_mesh.glb": ("texture", ".glb"),
    "oriented/mesh_upright.glb": ("orientation", ".glb"),
    "oriented/lego_input.ply": ("orientation", ".ply"),
}


def stage_for(relative: str) -> str:
    if relative in EDITABLE_ARTIFACTS:
        return EDITABLE_ARTIFACTS[relative][0]
    if re.fullmatch(r"lego-\d+/model\.mpd", relative) or re.fullmatch(
        r"ui_assistant/jobs/\d{8}-\d{6}-[0-9a-f]{8}/(?:bundle|optimized)/model/model\.mpd", relative
    ):
        return "lego"
    return ""


def expected_suffix(relative: str) -> str:
    if relative in EDITABLE_ARTIFACTS:
        return EDITABLE_ARTIFACTS[relative][1]
    if stage_for(relative) == "lego":
        return ".mpd"
    raise ValueError("这个产物暂不支持上传修改版")


def _state_path(run_dir: Path) -> Path:
    return run_dir / REVISION_DIR / "state.json"


@contextmanager
def _state_file_lock(path: Path):
    lock_path = path.with_suffix(".lock")
    with _STATE_THREAD_LOCK:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_state(run_dir: Path) -> dict[str, object]:
    path = _state_path(run_dir)
    try:
        # On Windows an open reader can prevent os.replace() from replacing
        # state.json, so readers must use the same lock as writers.
        with _state_file_lock(path):
            payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if path.is_file():
            raise ValueError(f"版本选择记录读取失败：{path}；不会自动退回初版") from exc
        return {"version": 1, "pairs": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("pairs"), dict):
        raise ValueError(f"版本选择记录格式无效：{path}；不会自动退回初版")
    return payload


def _save_state(run_dir: Path, payload: dict[str, object]) -> None:
    path = _state_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.{secrets.token_hex(8)}.tmp")
    with _state_file_lock(path):
        _write_state_locked(path, temporary, payload)
    _mark_view_changed(run_dir)


def _write_state_locked(path: Path, temporary: Path, payload: dict) -> None:
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            # Antivirus/indexers may briefly deny replacement on Windows.
            if os.name != "nt" or attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _mark_view_changed(run_dir: Path) -> None:
    import ui_run_state

    ui_run_state.mark_changed(run_dir, Path(__file__).resolve().parent)


def update_state(run_dir: Path, mutate) -> None:
    """Read/modify/write under one file lock, preserving concurrent choices/pointer."""
    path = _state_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _state_file_lock(path):
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or not isinstance(state.get("pairs"), dict):
                raise ValueError("修改选择记录无效，已停止保存")
        else:
            state = {"version": 1, "pairs": {}}
        mutate(state)
        temporary = path.with_name(f"{path.stem}.{secrets.token_hex(8)}.tmp")
        _write_state_locked(path, temporary, state)
    _mark_view_changed(run_dir)


def branch_parent(run_dir: Path, root: Path) -> Path | None:
    try:
        payload = json.loads((run_dir / BRANCH_FILE).read_text(encoding="utf-8"))
        parent = Path(payload["parent_run_dir"]).resolve()
        if parent.is_dir() and parent != run_dir.resolve() and parent.is_relative_to(root.resolve()):
            return parent
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def root_run_dir(run_dir: Path, root: Path) -> Path:
    """Return the user-facing run folder, keeping internal branches private."""
    current = run_dir.resolve()
    seen = {current}
    for _ in range(32):
        parent = branch_parent(current, root)
        if parent is None or parent in seen:
            break
        current = parent
        seen.add(current)
    return current


def remember_active_run(run_dir: Path, root: Path) -> None:
    """Persist the adopted branch on its public run, independent of this process."""
    run_dir = run_dir.resolve()
    if not run_dir.is_dir() or not run_dir.is_relative_to(root.resolve()):
        raise ValueError("当前运行目录必须位于项目内")
    public_run = root_run_dir(run_dir, root)
    if load_state(public_run).get("active_run_dir") != str(run_dir):
        update_state(public_run, lambda state: state.update(active_run_dir=str(run_dir)))
    import ui_run_state

    ui_run_state.remember(run_dir, root)


def restore_active_run(run_dir: Path, root: Path) -> Path:
    """Restore an adopted branch; migrate old state only when its choice is unique.

    Branch timestamps cannot tell us what the user accepted. Older versions
    retained the chosen input asset and branch metadata, so match those exact
    paths instead of guessing which output folder is newest.
    """
    run_dir = run_dir.resolve()
    root = root.resolve()
    if not run_dir.is_dir() or not run_dir.is_relative_to(root):
        return run_dir
    import ui_run_state

    if root_run_dir(run_dir, root) == run_dir:
        recorded = ui_run_state.active_run(run_dir, root)
        if recorded is not None:
            return recorded
    state = load_state(run_dir)
    saved = state.get("active_run_dir")
    if isinstance(saved, str):
        selected = Path(saved).resolve()
        if selected != run_dir and selected.is_dir() and selected.is_relative_to(root) and root_run_dir(selected, root) == run_dir:
            return selected
    current = run_dir
    seen = {current}
    for _ in range(32):
        matches: set[Path] = set()
        for relative, stage, field in (
            ("input/foreground.png", "input", "source_image"),
            ("unitex/mv_rgb.png", "texture", "six_view"),
        ):
            record = selected_pair(current, relative, root)
            if record is None or record.get("choice") != "candidate":
                continue
            selected_asset = Path(str(record["candidate"])).resolve()
            for metadata in (current / REVISION_DIR / "branches").glob(f"*/{BRANCH_FILE}"):
                try:
                    payload = json.loads(metadata.read_text(encoding="utf-8"))
                    branch = metadata.parent.resolve()
                    branch_stage = "texture" if payload.get("stage") == "texture" else "input"
                    if (branch_stage == stage and branch_parent(branch, root) == current
                            and Path(payload[field]).resolve() == selected_asset):
                        matches.add(branch)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        if len(matches) != 1:
            break
        selected = matches.pop()
        if selected in seen:
            break
        current = selected
        seen.add(current)
    return current


def branch_source_image(run_dir: Path, root: Path) -> Path | None:
    """Read an input-stage branch's accepted image without exposing it as form input."""
    if branch_start_stage(run_dir, root) != "input":
        return None
    try:
        payload = json.loads((run_dir / BRANCH_FILE).read_text(encoding="utf-8"))
        source = Path(payload["source_image"]).resolve()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return source if source.is_file() and source.is_relative_to(root.resolve()) else None


def branch_start_stage(run_dir: Path, root: Path) -> str | None:
    if branch_parent(run_dir, root) is None:
        return None
    try:
        payload = json.loads((run_dir / BRANCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return "input"
    return "texture" if payload.get("stage") == "texture" else "input"


def available_artifact(run_dir: Path, relative: str, root: Path) -> Path:
    current = run_dir.resolve()
    seen: set[Path] = set()
    for _ in range(32):
        if current in seen:
            break
        seen.add(current)
        candidate = (current / relative).resolve()
        if candidate.is_file() and candidate.is_relative_to(root.resolve()):
            return candidate
        parent = branch_parent(current, root)
        if parent is None:
            break
        current = parent
    raise ValueError("找不到该阶段的原版产物")


def add_candidate(
    run_dir: Path,
    relative: str,
    filename: str,
    content: bytes,
    root: Path,
    *,
    origin: str = "upload",
) -> dict[str, object]:
    if origin not in {"upload", "assistant"}:
        raise ValueError("无效的修改版来源")
    suffix = expected_suffix(relative)
    if Path(filename).suffix.lower() != suffix:
        raise ValueError(f"修改版必须是 {suffix} 文件")
    if not content:
        raise ValueError("修改版文件为空")
    original = available_artifact(run_dir, relative, root)
    six_view_lock = None
    if relative == "unitex/mv_rgb.png":
        from ui_six_view import constrain_candidate

        content, six_view_lock = constrain_candidate(run_dir, content, root)
    ident = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    target = run_dir / REVISION_DIR / "assets" / f"{ident}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    record = {
        "original": str(original),
        "candidate": str(target),
        "choice": None,
        "origin": origin,
        "created_at": time.time(),
    }
    if six_view_lock is not None:
        record["six_view_lock"] = six_view_lock
    update_state(run_dir, lambda state: state["pairs"].__setitem__(relative, record))
    return {"relative": relative, "candidate": str(target)}


def selected_pair(run_dir: Path, relative: str, root: Path) -> dict[str, object] | None:
    pairs = load_state(run_dir).get("pairs", {})
    record = pairs.get(relative) if isinstance(pairs, dict) else None
    if not isinstance(record, dict):
        return None
    try:
        original = Path(record["original"]).resolve()
        candidate = Path(record["candidate"]).resolve()
    except (KeyError, TypeError, ValueError):
        return None
    if not all(path.is_file() and path.is_relative_to(root.resolve()) for path in (original, candidate)):
        if record.get("choice") in {"original", "candidate"}:
            raise ValueError(f"已选择的版本关联文件缺失或无效：{relative}；不会自动退回初版")
        return None
    if record.get("choice") not in (None, "original", "candidate"):
        return None
    return record


def register_generated_pair(
    run_dir: Path, relative: str, original: Path, candidate: Path, root: Path
) -> dict[str, object]:
    expected_suffix(relative)
    original, candidate = original.resolve(), candidate.resolve()
    if not all(path.is_file() and path.is_relative_to(root.resolve()) for path in (original, candidate)):
        raise ValueError("新旧产物必须位于项目内")
    existing = selected_pair(run_dir, relative, root)
    if existing and existing.get("origin") in {"upload", "assistant"}:
        return existing
    stamp = candidate.stat().st_mtime_ns
    if existing and existing.get("candidate") == str(candidate) and existing.get("candidate_mtime_ns") == stamp:
        return existing
    record = {
        "original": str(original), "candidate": str(candidate), "candidate_mtime_ns": stamp,
        "choice": None, "origin": "generated", "created_at": time.time(),
    }
    update_state(run_dir, lambda state: state["pairs"].__setitem__(relative, record))
    return record


def choose(run_dir: Path, relative: str, choice: str, root: Path) -> dict[str, object]:
    if choice not in {"original", "candidate"}:
        raise ValueError("只能选择原版或修改版")
    if not stage_for(relative):
        raise ValueError("无效的产物")
    record = selected_pair(run_dir, relative, root)
    if record is None:
        raise ValueError("没有可选择的有效候选")
    if relative == "unitex/mv_rgb.png" and choice == "candidate":
        from ui_six_view import validate_candidate

        record["six_view_lock"] = validate_candidate(run_dir, Path(record["candidate"]).read_bytes(), root)
    def apply(state):
        current = state["pairs"].get(relative)
        if not current or current.get("candidate") != record["candidate"]:
            raise ValueError("候选已更新，请刷新后重新选择")
        current.update(record, choice=choice)
    update_state(run_dir, apply)
    record["choice"] = choice
    return record


def create_input_branch(run_dir: Path, image: Path, root: Path) -> Path:
    if not image.is_file() or not image.resolve().is_relative_to(root.resolve()):
        raise ValueError("修改版输入必须位于项目内")
    ident = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    branch = run_dir / REVISION_DIR / "branches" / ident
    branch.mkdir(parents=True, exist_ok=False)
    (branch / BRANCH_FILE).write_text(
        json.dumps(
            {"parent_run_dir": str(run_dir.resolve()), "source_image": str(image.resolve())},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return branch


def create_texture_branch(
    run_dir: Path, sheet: Path, source_image: Path, shape: Path, root: Path
) -> Path:
    for path in (run_dir, sheet, source_image, shape):
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("六视图分支只能引用项目内的文件")
    if not all(path.is_file() for path in (sheet, source_image, shape)):
        raise ValueError("六视图分支缺少原始图片、网格或修改版")
    ident = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    branch = run_dir / REVISION_DIR / "branches" / ident
    branch.mkdir(parents=True, exist_ok=False)
    (branch / BRANCH_FILE).write_text(
        json.dumps(
            {
                "parent_run_dir": str(run_dir.resolve()),
                "source_image": str(source_image.resolve()),
                "from_shape": str(shape.resolve()),
                "six_view": str(sheet.resolve()),
                "unitex_cache_run_dir": str(run_dir.resolve()),
                "stage": "texture",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return branch


def branch_settings(run_dir: Path, root: Path) -> dict[str, str] | None:
    try:
        payload = json.loads((run_dir / BRANCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("stage") != "texture":
        return None
    keys = ("source_image", "from_shape", "six_view", "unitex_cache_run_dir")
    try:
        paths = {key: Path(payload[key]).resolve() for key in keys}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(path.is_relative_to(root.resolve()) for path in paths.values()):
        return None
    if not all(paths[key].is_file() for key in keys[:-1]) or not paths[keys[-1]].is_dir():
        return None
    return {key: str(path) for key, path in paths.items()}
