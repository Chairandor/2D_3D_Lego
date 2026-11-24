"""Temporary model edit sessions and explicitly saved model variants."""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass

import proxe_integration as proxe
from model_edit_bridge import Client, copy_glb


def variant_dir(run: Path, model_id: str, section: str = "model_edits") -> Path:
    if not re.fullmatch(r"[0-9a-f]{24}", model_id):
        raise ValueError("无效的模型版本")
    folder = run / section / model_id
    if folder.resolve() != folder:
        raise ValueError("模型目录无效")
    return folder


def saved_model(run: Path, model_id: str) -> tuple[Path, str]:
    folder = variant_dir(run, model_id)
    path = folder / "model.glb"
    try:
        name = json.loads((folder / "model.json").read_text(encoding="utf-8"))["name"]
        if not path.is_file() or path.resolve() != path or not isinstance(name, str):
            raise ValueError("无效的已保存模型")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("找不到这个已保存的模型") from exc
    return path, name


def saved_models(run: Path):
    for folder in sorted((run / "model_edits").glob("*")):
        try:
            path, name = saved_model(run, folder.name)
        except ValueError:
            continue
        yield folder.name, path, name


def lego_runs(run: Path):
    for folder in sorted((run / "lego_sources").glob("*")):
        try:
            directory = variant_dir(run, folder.name, "lego_sources")
            name = json.loads((directory / "source.json").read_text(encoding="utf-8"))["name"]
            if isinstance(name, str):
                yield folder.name, directory, name
        except (OSError, ValueError, KeyError, TypeError):
            continue


@dataclass
class Session:
    run: Path | None
    workspace: Path
    source: Path
    name: str
    saved: Path | None = None
    remote_session: str = ""
    remote_version: str = ""

    def latest(self, state: dict | None = None) -> Path | None:
        if state is None:
            state = proxe.read_state(self.workspace)
        return proxe.safe_run_file(self.workspace, state.get("latest_result"))


class Sessions:
    def __init__(self, root: Path, editor, remote: Client | None = None):
        self.root = root
        self.storage = root / ".runtime" / "model-edit-sessions"
        self.editor = editor
        self.remote = remote
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}

    def open(self, run: Path, source: Path, name: str) -> dict:
        return self._open(run, name, lambda snapshot: shutil.copy2(source, snapshot))

    def open_uploaded(self, stream, length: int) -> dict:
        return self._open(None, "远程编辑模型", lambda snapshot: copy_glb(stream, snapshot, length))

    def _open(self, run: Path | None, name: str, write_source) -> dict:
        with self.lock:
            self.storage.mkdir(parents=True, exist_ok=True)
            token = secrets.token_hex(16)
            workspace = self.storage / token
            workspace.mkdir(mode=0o700)
            snapshot = workspace / "source.glb"
            try:
                write_source(snapshot)
            except (OSError, ValueError):
                shutil.rmtree(workspace)
                raise
            self.sessions[token] = Session(run, workspace, snapshot, name)
            return self.status(token)

    def get(self, token: str) -> Session:
        session = self.sessions.get(token)
        if session is None and re.fullmatch(r"[0-9a-f]{32}", token):
            workspace = self.storage / token
            try:
                record = json.loads((workspace / "recovery.json").read_text(encoding="utf-8"))
                run = Path(record["run"]).resolve()
                source = proxe.safe_run_file(workspace, workspace / "source.glb")
                if source and run.is_dir() and run.is_relative_to(self.root) and isinstance(record["name"], str):
                    session = self.sessions[token] = Session(run, workspace, source, record["name"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if not session:
            raise ValueError("编辑窗口已关闭，请重新打开模型")
        return session

    def preview(self, token: str) -> Path:
        with self.lock:
            session = self.get(token)
            return session.latest() or session.source

    def status(self, token: str) -> dict:
        with self.lock:
            session = self.get(token)
            editor = (self._remote_status(session) if self.remote and session.run is not None
                      else self.editor.status(session.workspace))
            state = proxe.read_state(session.workspace)
            latest = session.latest(state)
            path = latest or session.source
            message = editor["message"]
            if latest and message == "review":
                message = "已恢复临时结果，可以预览或另存"
            return {
                "session": token, "name": session.name, "running": editor["running"],
                "configured": editor["configured"], "message": message,
                "can_save": bool(session.run is not None and latest and latest != session.saved
                                 and not editor["running"]),
                "can_check": bool(latest and not editor["running"]),
                "preview_url": "/api/model-edit/preview?" + urllib.parse.urlencode({
                    "session": token, "version": path.stat().st_mtime_ns,
                }),
            }

    def _remote_status(self, session: Session) -> dict:
        if not session.remote_session:
            return {"configured": True, "running": False, "message": "等待编辑"}
        state = self.remote.action("status", session.remote_session)
        if state.get("can_check") and not state["running"]:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(state["preview_url"]).query)
            version = query.get("version", [""])[0]
            if not re.fullmatch(r"[0-9]{1,22}", version):
                raise RuntimeError("A800 未返回有效的模型版本")
            if version != session.remote_version:
                result = session.workspace / f"draft-{version}.glb"
                self.remote.download(session.remote_session, version, result)
                proxe.write_state(session.workspace, {"status": "review", "latest_result": str(result)})
                session.remote_version = version
        return state

    def edit(self, token: str, instruction: str) -> dict:
        with self.lock:
            session = self.get(token)
            if self.remote and session.run is not None:
                instruction = instruction.strip()
                if not instruction or len(instruction) > 2000:
                    raise ValueError("请输入 1 到 2000 个字符的修改要求")
                if not session.remote_session:
                    session.remote_session = self.remote.upload(session.source)
                self.remote.action("edit", session.remote_session, instruction=instruction)
            else:
                self.editor.start(session.workspace, {"instruction": instruction},
                                  source=session.latest() or session.source, prepare_lego=False)
            return self.status(token)

    def check(self, token: str) -> dict:
        with self.lock:
            session = self.get(token)
            status = self.status(token)
            if not status["can_check"]:
                raise ValueError("请先生成修改版再检查")
            if self.remote and session.remote_session:
                checked = self.remote.action("check", session.remote_session)
                return {**status, "checks": checked["checks"]}
            state = proxe.read_state(session.workspace)
            latest = session.latest(state)
            edit = state.get("latest_edit", {})
            source = proxe.safe_run_file(session.workspace, edit.get("source")) or session.source
            quality = proxe.validate_mesh(latest, require_watertight=False)
            if quality["watertight"]:
                checks = ["网格闭合，无非流形边。"]
            else:
                checks = [f"网格有 {quality['boundary_edges']} 条开口边、"
                          f"{quality['nonmanifold_edges']} 条非流形边，可能影响 LEGO 转换。"]
            if edit.get("route") == "proxy_addition":
                try:
                    proxe.validate_exact_source_preservation(source, latest)
                    checks.append("原模型的网格和贴图均已保留。")
                except (ValueError, OSError):
                    checks.append("新增部件后，未能确认原模型的网格和贴图完全保留。")
            elif edit.get("preserve_existing_texture", not edit):
                try:
                    proxe.validate_appearance_preservation(source, latest)
                    checks.append("整体配色与修改前接近。")
                except (ValueError, OSError):
                    checks.append("保色检查未通过，整体颜色可能与修改前有偏差。")
            return {**status, "checks": checks}

    def save(self, token: str, name: str) -> dict:
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("请输入 1 到 80 个字符的模型名称")
        with self.lock:
            session = self.get(token)
            if session.run is None or not self.status(token)["can_save"]:
                raise ValueError("当前没有可以保存的新结果")
            latest = session.latest()
            model_id = secrets.token_hex(12)
            folder = variant_dir(session.run, model_id)
            folder.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".saving-", dir=folder.parent) as temporary:
                staging = Path(temporary) / "model"
                staging.mkdir()
                shutil.copy2(latest, staging / "model.glb")
                (staging / "model.json").write_text(json.dumps({"name": name}, ensure_ascii=False), encoding="utf-8")
                staging.rename(folder)
            session.saved = latest
            return {"id": model_id, **self.status(token)}

    def close(self, token: str) -> None:
        with self.lock:
            try:
                session = self.get(token)
            except ValueError:
                return
            self.sessions.pop(token, None)
            if self.remote and session.remote_session:
                try:
                    self.remote.action("close", session.remote_session)
                finally:
                    shutil.rmtree(session.workspace, ignore_errors=True)
            elif self.editor.status(session.workspace)["running"]:
                self.editor.stop()

                def discard_when_stopped():
                    while self.editor.status(session.workspace)["running"]:
                        time.sleep(.25)
                    shutil.rmtree(session.workspace, ignore_errors=True)

                threading.Thread(target=discard_when_stopped, daemon=True).start()
            else:
                shutil.rmtree(session.workspace, ignore_errors=True)
