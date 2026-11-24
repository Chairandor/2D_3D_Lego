"""Transfer temporary model editor sessions over a loopback SSH tunnel."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import struct
import urllib.error
import urllib.parse
import urllib.request


MAX_GLB_SIZE = 120 * 1024 * 1024


def copy_glb(stream, target: Path, length: int) -> None:
    if not 20 <= length <= MAX_GLB_SIZE:
        raise ValueError("GLB 文件大小须在 20 字节到 120 MB 之间")
    header = stream.read(12)
    if len(header) != 12 or struct.unpack("<4sII", header) != (b"glTF", 2, length):
        raise ValueError("GLB 文件头无效")
    remaining = length - len(header)
    with target.open("wb") as result:
        result.write(header)
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("GLB 传输中断")
            result.write(chunk)
            remaining -= len(chunk)


def authorize_upload(header: str) -> None:
    token = os.environ.get("MODEL_EDIT_BRIDGE_TOKEN", "")
    if (not token or os.environ.get("MODEL_EDIT_BRIDGE_URL")
            or not hmac.compare_digest(header.encode(), f"Bearer {token}".encode())):
        raise PermissionError("3D 编辑桥接未授权")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, url: str, token: str):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
                or not parsed.port or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise ValueError("MODEL_EDIT_BRIDGE_URL 须指向本机 SSH 隧道")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{32,128}", token):
            raise ValueError("MODEL_EDIT_BRIDGE_TOKEN 格式无效")
        self.url = url.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    @classmethod
    def from_environment(cls):
        url = os.environ.get("MODEL_EDIT_BRIDGE_URL", "").strip()
        return cls(url, os.environ.get("MODEL_EDIT_BRIDGE_TOKEN", "")) if url else None

    def _request(self, path: str, data=None, headers=None, timeout: int = 12):
        headers = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        req = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            return self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            with exc:
                try:
                    error = json.loads(exc.read(8192)).get("error", "")
                except (ValueError, AttributeError):
                    error = ""
            raise RuntimeError(f"A800 3D 编辑请求失败（HTTP {exc.code}）{error}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("无法连接 A800 的 3D 编辑服务，请检查 SSH 隧道") from exc

    def upload(self, file: Path) -> str:
        length = file.stat().st_size
        if not 20 <= length <= MAX_GLB_SIZE:
            raise ValueError("GLB 文件大小须在 20 字节到 120 MB 之间")
        with file.open("rb") as stream, self._request(
            "/api/model-edit/bridge-open", stream,
            {"Content-Type": "model/gltf-binary", "Content-Length": str(length)}, timeout=180,
        ) as response:
            result = json.load(response)
        session = result.get("session", "")
        if not re.fullmatch(r"[0-9a-f]{32}", session):
            raise RuntimeError("A800 未返回有效的编辑会话")
        return session

    def action(self, action: str, session: str, **fields) -> dict:
        body = json.dumps({"session": session, **fields}, ensure_ascii=False).encode()
        with self._request("/api/model-edit/" + action, body, {"Content-Type": "application/json"},
                           timeout=45 if action == "check" else 12) as response:
            return json.load(response)

    def download(self, session: str, version: str, target: Path) -> None:
        query = urllib.parse.urlencode({"session": session, "version": version})
        partial = target.with_suffix(".partial")
        try:
            with self._request("/api/model-edit/preview?" + query, timeout=180) as response:
                length = int(response.headers.get("Content-Length", "0"))
                copy_glb(response, partial, length)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
