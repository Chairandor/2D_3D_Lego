"""仅绑定回环地址的本地工作台，使用标准库管理既有 CLI 任务。"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit
import uuid
import webbrowser

from legolizer.ui.workflows import BY_ID, OPERATIONS, build_stages, validated_options
from legolizer import __version__

ASSETS = Path(__file__).with_name("static")
FORMATS = {".stl", ".ply", ".obj", ".off", ".gltf", ".glb", ".pcd", ".xyz",
           ".xyzn", ".xyzrgb", ".pts", ".npz", ".ldr", ".mpd", ".json", ".tgz", ".gz", ".csv"}
MAX_UPLOAD = 128 * 1024 * 1024


def workspace_path(root: Path, value: str, *, must_exist: bool = True) -> Path:
    """解析工作区内路径；拒绝隐藏目录、越界路径与指向外部的符号链接。"""
    candidate = Path(value)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError:
        raise ValueError("请选择项目文件夹内的文件；外部单文件可通过上传导入") from None
    if any(part.startswith(".") for part in relative.parts):
        raise ValueError("不读取隐藏目录或配置文件")
    if must_exist and not path.exists():
        raise ValueError(f"找不到文件或目录：{relative}")
    return path


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def inventory(root: Path, folder: Path) -> list[dict]:
    """列出本次运行的可下载产物；不跟随工作区外部链接。"""
    result = []
    for path in sorted(folder.rglob("*")):
        if "work" in path.relative_to(folder).parts:
            continue  # 严格后端的候选与求解暂存文件不作为最终产物展示。
        if path.suffix.lower() not in FORMATS or not path.is_file():
            continue
        try:
            path = workspace_path(root, str(path))
        except ValueError:
            continue
        rel = relative(root, path)
        can_input = path.suffix.lower() in {".stl", ".ply", ".obj", ".off", ".gltf", ".glb", ".pcd", ".xyz", ".npz"} or path.name == "manifest.json"
        result.append(dict(path=rel, name=relative(folder, path), size=path.stat().st_size, can_input=can_input,
                           url="/file?path=" + quote(rel)))
        if len(result) >= 250:
            break
    return result


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def reports(folder: Path) -> list[dict]:
    """直接读取原始报告，不用退出码推断认证结论。"""
    result = []
    for name in ("bundle.json", "backend_report.json", "report.json"):
        for path in sorted(folder.rglob(name)):
            value = read_json(path)
            if name == "report.json":
                strict = value.get("legolization")
                if strict:
                    result.append(dict(kind="strict", **strict))
                elif value.get("packing"):
                    result.append(dict(kind="legacy", **{k: value[k] for k in ("packing", "assembly", "voxel") if k in value}))
            elif name == "backend_report.json" and value.get("backend") == "legolization":
                result.append(dict(kind="strict", **value))
            elif name == "bundle.json":
                stages = value.get("stages", {})
                audit = path.parent / "instructions" / "audit.json"
                audit_data = read_json(audit) if audit.is_file() else {}
                result.append(dict(kind="strict", bundle_status=value.get("status"),
                    **value.get("verdicts", {}),
                    instructions_status=stages.get("instructions", {}).get("status"),
                    brick_count=audit_data.get("input", {}).get("brick_count") or stages.get("generate", {}).get("detail", {}).get("brick_count"),
                    instruction_step_count=audit_data.get("input", {}).get("step_count"),
                    instruction_audit_valid=audit_data.get("certification", {}).get("valid"),
                    warnings=value.get("warnings", [])))
    # 同一运行的 report/backend_report/bundle 描述同一个判定；优先原始 bundle 审计。
    strict = [item for item in result if item["kind"] == "strict"]
    return strict[:1] if strict else result


class Controller:
    """串行任务控制器；线程锁覆盖启动、取消和终态写入。"""

    def __init__(self, root: Path, python: str = sys.executable) -> None:
        self.root = root.resolve()
        self.python = python
        self.runs = self.root / "artifacts" / "ui_runs"
        self.lock = threading.RLock()
        self.job: dict | None = None
        self.logs: deque[str] = deque(maxlen=2000)
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()

    def start(self, payload: dict) -> dict:
        op_id = payload.get("operation")
        if op_id not in BY_ID:
            raise ValueError("请选择支持的转换路径")
        source = workspace_path(self.root, str(payload.get("input", "")))
        if not source.is_file():
            raise ValueError("请选择输入文件")
        options = payload.get("options", {})
        if not isinstance(options, dict):
            raise ValueError("参数必须为对象")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        output = self.runs / run_id
        stages = build_stages(op_id, source, output, options)
        if BY_ID[op_id]["accepts"] == "rgbd":
            data = read_json(source)
            if not isinstance(data, dict) or not data.get("frames"):
                raise ValueError("请选择包含非空 frames 的 RGB-D manifest JSON")
        if op_id == "legalize":
            from legolizer.processing3d.volume import VoxelVolume
            volume = VoxelVolume.load(source)
            if any(abs(a - b) > 1e-6 for a, b in zip(volume.voxel_size_mm, (8.0, 8.0, 9.6))):
                raise ValueError("严格 LEGO 需要 8×8×9.6 mm、Z-up 的 NPZ，请先重新体素化")
        with self.lock:
            if self.job and self.job["status"] in {"running", "stopping"}:
                raise RuntimeError("已有任务运行中，请等待或停止当前任务")
            output.mkdir(parents=True, exist_ok=False)
            self.cancel.clear()
            self.logs.clear()
            self.job = dict(id=run_id, operation=op_id, input=relative(self.root, source),
                            legolizer_version=__version__, python=self.python,
                            options=validated_options(BY_ID[op_id], options),
                            output=relative(self.root, output), status="running", started=time.time(),
                            ended=None, stages=stages, message="任务已启动")
            self._save()
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
            return dict(id=run_id)

    def _save(self) -> None:
        path = self.root / self.job["output"] / "job.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.job, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _run(self) -> None:
        job = self.job
        output = self.root / job["output"]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "src") + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            with (output / "run.log").open("w", encoding="utf-8", buffering=1) as log:
                for stage in job["stages"]:
                    with self.lock:
                        if self.cancel.is_set():
                            break
                        stage["state"] = "running"
                        command = [self.python, "-u", "-m", "legolizer", *stage["args"]]
                        line = "$ " + subprocess.list2cmdline(command)
                        self.logs.append(line)
                        log.write(line + "\n")
                        self._save()
                        self.process = subprocess.Popen(command, cwd=self.root, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            start_new_session=os.name != "nt")
                        process = self.process
                    for line in process.stdout:
                        log.write(line)
                        with self.lock:
                            self.logs.append(line.rstrip())
                    code = process.wait()
                    with self.lock:
                        self.process = None
                        stage["returncode"] = code
                        stage["state"] = "cancelled" if self.cancel.is_set() else "complete" if code == 0 else "partial" if code in {2, 3, 4} else "failed"
                    if code or self.cancel.is_set():
                        break
                with self.lock:
                    if self.cancel.is_set():
                        job.update(status="cancelled", message="任务已停止；已生成的文件保留供检查")
                    elif all(s["state"] == "complete" for s in job["stages"]):
                        job.update(status="complete", message="转换执行完成；LEGO 认证请查看独立检查结果")
                    elif any(s["state"] == "partial" for s in job["stages"]):
                        job.update(status="partial", message="后端返回部分结果或未通过判定；请查看报告和日志")
                    else:
                        job.update(status="failed", message="转换失败，请查看日志末尾的原因")
        except Exception as exc:
            with self.lock:
                self.logs.append(f"错误：{exc}")
                job.update(status="failed", message=str(exc))
            with (output / "run.log").open("a", encoding="utf-8") as log:
                log.write(f"错误：{exc}\n")
        finally:
            with self.lock:
                job["ended"] = time.time()
                self._save()

    def stop(self) -> None:
        with self.lock:
            if not self.job or self.job["status"] not in {"running", "stopping"}:
                return
            self.cancel.set()
            self.job.update(status="stopping", message="正在停止进程树……")
            process = self.process
            if process and process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                else:
                    import signal
                    os.killpg(process.pid, signal.SIGTERM)

    def snapshot(self) -> dict:
        with self.lock:
            job = json.loads(json.dumps(self.job)) if self.job else None
            logs = "\n".join(self.logs)
        result = dict(job=job, logs=logs, artifacts=[], reports=[])
        if job:
            result["elapsed"] = round((job["ended"] or time.time()) - job["started"], 1)
            folder = self.root / job["output"]
            result["artifacts"] = inventory(self.root, folder)
            if job["ended"]:
                result["reports"] = reports(folder)
            else:
                for path in folder.rglob("bundle.json"):
                    try:
                        bundle = read_json(path)
                        stages = bundle.get("stages", {})
                        running = [name for name, stage in stages.items() if stage.get("status") == "running"]
                        result["backend_progress"] = "legolization · " + (", ".join(running) or bundle.get("status", "运行中"))
                    except (OSError, ValueError):
                        # 后端可能正写此 JSON，下一次轮询再读取。
                        continue
        return result


def make_server(root: Path, port: int = 8765, python: str = sys.executable) -> ThreadingHTTPServer:
    """创建可测试的回环 HTTP 服务；port=0 可交由系统分配端口。"""
    root = root.resolve()
    controller = Controller(root, python)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def send(self, value, status: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def same_origin(self) -> None:
            port = self.server.server_port
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in allowed:
                raise ValueError("仅接受本机工作台请求")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in allowed}:
                raise ValueError("不接受跨站请求")

        def do_GET(self) -> None:
            try:
                self.same_origin()
                url = urlsplit(self.path)
                args = parse_qs(url.query)
                value = lambda key, default="": args.get(key, [default])[0]
                if url.path in {"/", "/app.js", "/style.css"}:
                    name = "index.html" if url.path == "/" else url.path[1:]
                    content_types = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}
                    # Windows 注册表可能把 .js 映射成 text/plain；nosniff 会因此拒绝执行。
                    self.send((ASSETS / name).read_bytes(), content_type=content_types[name] + "; charset=utf-8")
                elif url.path == "/api/bootstrap":
                    self.send(dict(root=str(root), python=python, operations=OPERATIONS,
                        dependencies={name: importlib.util.find_spec(name) is not None for name in ("numpy", "open3d", "trimesh", "scipy", "skimage", "h5py")},
                        strict_backend=(root / "research/opensource/legolization/.venv/Scripts/legolization.exe").is_file()
                            or (root / "research/opensource/legolization/.venv/bin/legolization").is_file() or shutil.which("uv") is not None))
                elif url.path == "/api/status":
                    self.send(controller.snapshot())
                elif url.path == "/api/browse":
                    folder = workspace_path(root, value("path", "."))
                    entries = []
                    for path in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                        try:
                            path = workspace_path(root, str(path))
                            if path.is_dir() or path.suffix.lower() in FORMATS:
                                entries.append(dict(name=path.name, path=relative(root, path), directory=path.is_dir(),
                                    size=0 if path.is_dir() else path.stat().st_size))
                        except (OSError, ValueError):
                            continue
                    self.send(dict(path=relative(root, folder), entries=entries))
                elif url.path == "/api/preview":
                    from legolizer.ui.scene import scene
                    self.send(scene(workspace_path(root, value("path")), value("axis", "z")))
                elif url.path == "/api/history":
                    jobs = []
                    for path in sorted(controller.runs.glob("*/job.json"), reverse=True)[:30]:
                        try:
                            jobs.append(read_json(path))
                        except (OSError, ValueError):
                            continue
                    self.send(jobs)
                elif url.path == "/api/result":
                    folder = workspace_path(root, value("path"))
                    if not folder.is_dir() or not folder.is_relative_to(controller.runs):
                        raise ValueError("请选择工作台的历史运行目录")
                    self.send(dict(artifacts=inventory(root, folder), reports=reports(folder)))
                elif url.path == "/file":
                    path = workspace_path(root, value("path"))
                    if path.suffix.lower() not in FORMATS | {".log"} or not path.is_file():
                        raise ValueError("此文件不支持下载")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name))
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    with path.open("rb") as handle:
                        shutil.copyfileobj(handle, self.wfile)
                else:
                    self.send(dict(error="接口不存在"), 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self.send(dict(error=str(exc)), 400)

        def do_POST(self) -> None:
            try:
                self.same_origin()
                if self.headers.get("X-Legolizer-Request") != "1":
                    raise ValueError("缺少工作台请求标记")
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > MAX_UPLOAD:
                    raise ValueError("请求为空或超过 128 MiB；大文件请复制到项目目录后选择")
                url = urlsplit(self.path)
                if url.path == "/api/upload":
                    name = parse_qs(url.query).get("name", ["input.ply"])[0].replace("\\", "/").split("/")[-1]
                    name = re.sub(r"[^\w.\-]", "_", name).strip(".") or "input.ply"
                    if Path(name).suffix.lower() not in FORMATS - {".json", ".gltf"}:
                        raise ValueError("请上传单个几何文件；manifest / glTF 连同依赖请复制到项目文件夹")
                    folder = root / "artifacts" / "ui_uploads" / uuid.uuid4().hex[:12]
                    folder.mkdir(parents=True, exist_ok=False)
                    path = folder / name
                    remaining = size
                    with path.open("wb") as handle:
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("上传不完整，请重新上传")
                            handle.write(chunk)
                            remaining -= len(chunk)
                    self.send(dict(path=relative(root, path)))
                    return
                if size > 65536:
                    raise ValueError("任务参数超过大小限制")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("请求参数必须为对象")
                if url.path == "/api/start":
                    self.send(controller.start(payload))
                elif url.path == "/api/stop":
                    controller.stop()
                    self.send(dict(ok=True))
                else:
                    self.send(dict(error="接口不存在"), 404)
            except RuntimeError as exc:
                self.send(dict(error=str(exc)), 409)
            except Exception as exc:
                self.send(dict(error=str(exc)), 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.controller = controller
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LEGOLIZER 本地交互工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--python", default=sys.executable, help="执行转换的 Python，默认当前解释器")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    if not (args.root / "src/legolizer/cli.py").is_file():
        parser.error("请在 LEGOLIZER 根目录运行，或用 --root 指定项目目录")
    try:
        server = make_server(args.root, args.port, args.python)
    except OSError as exc:
        parser.error(f"无法启动服务：{exc}；可用 --port 换一个端口")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"LEGOLIZER 工作台：{url}\n转换解释器：{args.python}\n按 Ctrl+C 停止服务。", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.controller.stop()
        if server.controller.worker:
            server.controller.worker.join(timeout=15)
        server.server_close()
    return 0
