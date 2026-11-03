"""在原工作台中运行三条本地 3D 文件流程；重依赖留在独立子进程。"""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse

from project_config import terminate_process_tree


ROOT = Path(__file__).resolve().parent
_core_path = Path(os.environ.get("LEGOLIZER_ROOT", str(ROOT.parent)))
CORE_ROOT = (_core_path if _core_path.is_absolute() else ROOT / _core_path).resolve()
if str(CORE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(CORE_ROOT / "src"))

try:
    from legolizer.processing3d.flow_config import FLOWS, validate_options
    CORE_AVAILABLE = True
except ModuleNotFoundError:
    CORE_AVAILABLE = False
    FLOWS = {
        "stl": dict(label="STL → LEGO", demo="bunny.stl", unit=1000., axis="y"),
        "ply_plain": dict(label="无色 PLY → Mesh → LEGO", demo="rhino.ply", unit=1., axis="y"),
        "ply_color": dict(label="有色 PLY → Mesh / 贴图 → LEGO", demo="tiko_10000_points.ply", unit=1., axis="y"),
    }

    def validate_options(flow, options):
        raise ValueError("请配置 LEGOLIZER_ROOT 指向三维模块项目，并设置 PROCESSING3D_PYTHON")


def find_python() -> str:
    if os.environ.get("PROCESSING3D_PYTHON"):
        return os.environ["PROCESSING3D_PYTHON"]
    registrations = Path.home() / ".conda/environments.txt"
    if registrations.is_file():
        for value in registrations.read_text(encoding="utf-8").splitlines():
            environment = Path(value)
            candidate = environment / ("python.exe" if os.name == "nt" else "bin/python")
            if environment.name == "legolizer" and candidate.is_file():
                return str(candidate)
    return sys.executable


def find_ldraw(root: Path = ROOT) -> Path:
    """只复用已经存在的零件库，不自动下载。"""
    choices = [Path(os.environ["LDRAW_LIBRARY"]) ] if os.environ.get("LDRAW_LIBRARY") else []
    choices.extend([root / "LDrawLibrary/ldraw"])
    if os.environ.get("LOCALAPPDATA"):
        choices.append(Path(os.environ["LOCALAPPDATA"]) / "pyldraw3/pyldraw3/Cache/complete/ldraw")
    choices.extend([Path.home() / ".cache/pyldraw3/complete/ldraw"])
    return next((p for p in choices if (p / "parts").is_dir() and (p / "LDConfig.ldr").is_file()), choices[0])


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{secrets.token_hex(5)}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
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


class ThreeDController:
    def __init__(self, root: Path = ROOT, core_root: Path = CORE_ROOT, python: str | None = None):
        self.root, self.core_root = root.resolve(), core_root.resolve()
        self.python = python or find_python()
        self.runs = self.root / "runs/three_d"
        self.lock = threading.RLock()
        self.process = None
        self.worker = None
        self.running = False
        self.cancel = threading.Event()
        self.current = {}
        self.logs = deque(maxlen=2000)

    def is_running(self) -> bool:
        with self.lock:
            return self.running

    def demo(self, flow: str) -> Path:
        if flow not in FLOWS:
            raise ValueError("无效的转换路径")
        return self.core_root / "datasets/others" / FLOWS[flow]["demo"]

    def bootstrap(self) -> dict:
        return dict(python=self.python, core_available=CORE_AVAILABLE, flows=[dict(id=k, **v, available=self.demo(k).is_file()) for k, v in FLOWS.items()],
                    has_ldraw=(find_ldraw(self.root) / "parts").is_dir(),
                    note="Rhino 文件虽带 RGB，本路径显式忽略颜色；Tiko 保留 RGB 并烘焙 UV 贴图")

    def _safe_run(self, value: str) -> Path:
        path = Path(value).resolve()
        if not path.is_relative_to(self.runs.resolve()) or path == self.runs.resolve():
            raise ValueError("请选择本工作台的 3D 运行记录")
        if any(p.startswith(".") for p in path.relative_to(self.runs).parts):
            raise ValueError("不能载入隐藏目录")
        return path

    def upload(self, name: str, data: bytes, flow: str) -> str:
        if self.is_running():
            raise RuntimeError("3D 转换正在运行，请先停止")
        suffix = ".stl" if flow == "stl" else ".ply" if flow in FLOWS else ""
        if not suffix or Path(name).suffix.lower() != suffix or not data or len(data) > 128 * 1024 ** 2:
            raise ValueError(f"请选择不超过 128 MiB 的 {suffix or '有效'} 文件")
        clean = re.sub(r"[^a-zA-Z0-9_.-]", "-", Path(name).name).strip(".") or ("upload" + suffix)
        folder = self.root / "ui_uploads/three_d" / (time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(4))
        folder.mkdir(parents=True)
        target = folder / clean
        target.write_bytes(data)
        return str(target)

    def start(self, payload: dict) -> dict:
        flow = str(payload.get("flow", "stl"))
        if not isinstance(payload.get("options", {}), dict):
            raise ValueError("处理参数必须是对象")
        options = validate_options(flow, payload.get("options", {}))
        source = self.demo(flow) if not payload.get("source") or payload.get("source") == "demo" else Path(str(payload["source"])).resolve()
        if not source.is_file() or not (source.is_relative_to(self.core_root) or source.is_relative_to(self.root)):
            raise ValueError("输入不存在或位于项目外；本机文件请通过上传选择")
        if source.suffix.lower() != (".stl" if flow == "stl" else ".ply"):
            raise ValueError("输入格式与路径不匹配")
        if any(part.startswith(".") for part in source.relative_to(self.core_root if source.is_relative_to(self.core_root) else self.root).parts):
            raise ValueError("不读取隐藏文件")
        with self.lock:
            if self.running:
                raise RuntimeError("已有 3D 任务运行中")
            output = self.runs / (time.strftime("%Y%m%d_%H%M%S") + "_" + flow + "_" + secrets.token_hex(4))
            self.current = dict(flow=flow, output=str(output), source=str(source), options=options,
                                status="starting", started=time.time(), ended=None,
                                is_demo=source.resolve() == self.demo(flow).resolve(),
                                prepare_only=bool(payload.get("prepare_only", False)), message="启动 3D 流水线")
            self.cancel.clear()
            self.logs.clear()
            self.running = True
            self.runs.mkdir(parents=True, exist_ok=True)
            _write(self.runs / "selection.json", {flow: str(output), **{
                k: v for k, v in _read(self.runs / "selection.json").items() if k != flow}})
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
            return dict(output=str(output), flow=flow)

    def _run(self):
        job = self.current
        output = Path(job["output"])
        command = [self.python, "-m", "legolizer.processing3d.file_pipeline", "--source", job["source"],
                   "--output", str(output), "--flow", job["flow"], "--options", json.dumps(job["options"])]
        if job["prepare_only"]:
            command.append("--prepare-only")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.core_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"], env["PYTHONIOENCODING"] = "1", "utf-8"
        try:
            with self.lock:
                if self.cancel.is_set():
                    return
                self.logs.append(subprocess.list2cmdline(command))
                kwargs = dict(cwd=self.core_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace")
                if os.name == "nt":
                    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                else:
                    kwargs["start_new_session"] = True
                self.process = subprocess.Popen(command, **kwargs)
                process = self.process
            for line in process.stdout:
                with self.lock:
                    self.logs.append(line.rstrip())
            code = process.wait()
            with self.lock:
                job["returncode"] = code
                if self.cancel.is_set():
                    job.update(status="cancelled", message="任务已停止")
                elif not (output / "pipeline3d.json").is_file():
                    job.update(status="failed", message="子进程未生成阶段记录，请查看日志")
                else:
                    job.update(_read(output / "pipeline3d.json"))
        except Exception as exc:
            with self.lock:
                self.logs.append(str(exc))
                job.update(status="failed", message=str(exc))
        finally:
            with self.lock:
                job["ended"] = time.time()
                output.mkdir(parents=True, exist_ok=True)
                if self.cancel.is_set():
                    state = _read(output / "pipeline3d.json") or dict(job)
                    state.update(status="cancelled", ended=job["ended"], message="用户停止，保留已生成产物")
                    for stage in state.get("stages", []):
                        if stage["status"] == "running":
                            stage["status"] = "cancelled"
                    _write(output / "pipeline3d.json", state)
                elif not (output / "pipeline3d.json").is_file():
                    _write(output / "pipeline3d.json", job)
                (output / "run.log").write_text("\n".join(self.logs), encoding="utf-8")
                self.running, self.process = False, None

    def stop(self):
        with self.lock:
            self.cancel.set()
            process = self.process
        if process is not None:
            terminate_process_tree(process)

    def load(self, flow: str, value: str) -> dict:
        if self.is_running():
            raise RuntimeError("任务运行时不能切换记录")
        run = self._safe_run(value)
        state = _read(run / "pipeline3d.json")
        if state.get("flow") != flow:
            raise ValueError("运行记录与当前转换路径不符")
        selections = _read(self.runs / "selection.json")
        selections[flow] = str(run)
        _write(self.runs / "selection.json", selections)
        with self.lock:
            self.current = {}
            self.logs.clear()
        return self.status(flow)

    def load_demo(self, flow: str) -> dict:
        rows = [r for r in self.history(flow) if _read(Path(r["output"]) / "pipeline3d.json").get("source") == str(self.demo(flow).resolve())]
        rows.sort(key=lambda r: (bool(_read(Path(r["output"]) / "pipeline3d.json").get("report", {}).get("legolization", {}).get("model_path")), r.get("started", 0)), reverse=True)
        if rows:
            return self.load(flow, rows[0]["output"])
        return self.start(dict(flow=flow, source="demo", prepare_only=True))

    def history(self, flow: str) -> list:
        result = []
        for path in sorted(self.runs.glob("*/pipeline3d.json"), reverse=True):
            try:
                data = _read(path)
                if data.get("flow") == flow:
                    result.append(dict(output=str(path.parent), status=data.get("status"),
                                       source=Path(data.get("source", "")).name,
                                       started=data.get("started"), options=data.get("options", {})))
            except (OSError, ValueError):
                pass
        return result[:40]

    def status(self, flow: str = "stl") -> dict:
        if flow not in FLOWS:
            raise ValueError("无效的路径")
        with self.lock:
            job = dict(self.current) if self.current.get("flow") == flow else {}
            selections = _read(self.runs / "selection.json")
            run = Path(job["output"]) if job else Path(selections[flow]) if selections.get(flow) else None
            if run is not None:
                run = self._safe_run(str(run))
                state = _read(run / "pipeline3d.json") or job
            else:
                state = dict(flow=flow, status="idle", assets=[], stages=[], message="载入默认示例或选择文件")
            state = dict(state)
            state["running"] = bool(self.running and job)
            state["busy"] = self.running
            state["elapsed"] = round((state.get("ended") or time.time()) - state.get("started", time.time()), 1)
            state["logs"] = "\n".join(self.logs) if job else ((run / "run.log").read_text(encoding="utf-8", errors="replace")[-25000:] if run and (run / "run.log").is_file() else "")
            if state.get("status") in {"running", "starting"} and not state["running"]:
                state.update(status="interrupted", message="此前任务已中断，保留现有阶段产物")
            state["assets"] = [dict(asset, url="/file?" + urllib.parse.urlencode({"path": asset["path"]})) for asset in state.get("assets", [])
                               if Path(asset["path"]).is_file() and Path(asset["path"]).resolve().is_relative_to(self.runs.resolve())]
            summary = state.get("report", {}).get("legolization", {})
            # 旧运行也可下载已存在的 BOM / 审计，无需重新求解。
            if summary.get("bundle_path"):
                bundle = Path(summary["bundle_path"]).resolve()
                existing = {asset["id"] for asset in state["assets"]}
                for key, relative, label in (("bom", "bom/bom.json", "BOM 零件清单"),
                                             ("audit", "instructions/audit.json", "装配审计报告")):
                    path = bundle.parent / relative
                    if key not in existing and path.is_file() and path.resolve().is_relative_to(self.runs.resolve()):
                        state["assets"].append(dict(id=key, label=label, path=str(path), kind="file", stage="assembly",
                                                    url="/file?" + urllib.parse.urlencode({"path": str(path)})))
            audit = summary.get("instruction_audit_path")
            if audit:
                candidate = Path(audit).resolve()
                state["assembly"] = _read(candidate) if candidate.is_relative_to(self.runs.resolve()) else {}
            state["history"] = self.history(flow)
            return state
