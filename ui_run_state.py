"""Authoritative per-project UI view, independent of output directory discovery."""
from __future__ import annotations

import copy
import json
import secrets
import time
import urllib.parse
from pathlib import Path

import ui_revisions

STATE_FILE = "ui_current_state.json"


def _public(run: Path, root: Path) -> Path:
    run = run.resolve()
    if not run.is_dir() or not run.is_relative_to(root.resolve()):
        raise ValueError("当前状态目录必须位于项目内")
    return ui_revisions.root_run_dir(run, root)


def _read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("active_run"), str):
            raise ValueError("格式无效")
        return data
    except (OSError, ValueError) as exc:
        raise ValueError(f"当前状态记录无法读取：{path}；已停止恢复，不会自动退回初版") from exc


def _write(path: Path, data: dict) -> None:
    temporary = path.with_name(f"{path.stem}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(8):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _path(value: str, public: Path, root: Path) -> Path:
    path = (public / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("当前状态引用了项目外的文件")
    return path


def _relative(path: Path, public: Path) -> str:
    import os

    return Path(os.path.relpath(path.resolve(), public.resolve())).as_posix()


def active_run(public: Path, root: Path) -> Path | None:
    public = _public(public, root)
    path = public / STATE_FILE
    with ui_revisions._state_file_lock(path):
        data = _read(path)
    if data is None:
        return None
    active = _path(data["active_run"], public, root)
    if not active.is_dir() or ui_revisions.root_run_dir(active, root) != public:
        raise ValueError(f"当前状态记录的分支已丢失：{active}；不会自动退回初版")
    return active


def remember(run: Path, root: Path) -> None:
    public = _public(run, root)
    path = public / STATE_FILE
    with ui_revisions._state_file_lock(path):
        data = _read(path)
        relative = _relative(run, public)
        if data and data["active_run"] == relative:
            return
        _write(path, {"version": 1, "active_run": relative, "needs_refresh": True,
                      "updated_at": time.time(), "artifact_groups": [], "pipeline_state": {}})


def mark_changed(run: Path, root: Path) -> None:
    """Only explicit revision writes invalidate a saved view; reads never do."""
    public = _public(run, root)
    path = public / STATE_FILE
    if not path.is_file():
        return
    with ui_revisions._state_file_lock(path):
        data = _read(path)
        data["needs_refresh"] = True
        data["updated_at"] = time.time()
        _write(path, data)


def save_view(run: Path, root: Path, groups: list, pipeline_state: dict) -> None:
    public = _public(run, root)
    encoded = copy.deepcopy(groups)
    for group in encoded:
        for key in ("original", "candidate"):
            item = group.get(key)
            if item:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(item.pop("url")).query)
                item["file"] = _relative(Path(query["path"][0]), public)
    path = public / STATE_FILE
    with ui_revisions._state_file_lock(path):
        _write(path, {"version": 1, "active_run": _relative(run, public),
                      "needs_refresh": False, "updated_at": time.time(),
                      "artifact_groups": encoded, "pipeline_state": pipeline_state})


def load_view(run: Path, root: Path) -> dict | None:
    public = _public(run, root)
    path = public / STATE_FILE
    with ui_revisions._state_file_lock(path):
        data = _read(path)
    if data is None or data.get("needs_refresh") or data["active_run"] != _relative(run, public):
        return None
    if not isinstance(data.get("artifact_groups"), list) or not isinstance(data.get("pipeline_state"), dict):
        raise ValueError("当前状态记录不完整；不会重新猜测原版或修改版")
    for group in data["artifact_groups"]:
        for key in ("original", "candidate"):
            item = group.get(key)
            if not item:
                continue
            source = _path(item.pop("file"), public, root)
            if not source.is_file():
                raise ValueError(f"当前状态中的产物已丢失：{source}；不会自动换成初版")
            item["url"] = "/file?" + urllib.parse.urlencode({"path": str(source)})
            item["size"] = source.stat().st_size
            item["mtime_ns"] = source.stat().st_mtime_ns
    return data
