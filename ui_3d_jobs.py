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

from project_config import resolve_core_root, terminate_process_tree
import ui_openmask


ROOT = Path(__file__).resolve().parent
CORE_ROOT = resolve_core_root(ROOT)
if str(CORE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(CORE_ROOT / "src"))

try:
    from legolizer.processing3d import flow_config
    FLOWS, validate_options = flow_config.FLOWS, flow_config.validate_options
    POINT_CLOUD_MESH_PROFILE = getattr(flow_config, "POINT_CLOUD_MESH_PROFILE", None)
    CORE_AVAILABLE = True
except ModuleNotFoundError:
    CORE_AVAILABLE = False
    POINT_CLOUD_MESH_PROFILE = None
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
        self.openmask_lock = threading.Lock()
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
        datasets = self.core_root / "datasets/others"
        default = datasets / FLOWS[flow]["demo"]
        sparse_tiko = datasets / "tiko_10000_points.ply"
        return sparse_tiko if flow == "ply_color" and not default.is_file() and sparse_tiko.is_file() else default

    def bootstrap(self) -> dict:
        return dict(python=self.python, core_available=CORE_AVAILABLE,
                    flows=[dict(v, id=k, demo=self.demo(k).name, available=self.demo(k).is_file()) for k, v in FLOWS.items()],
                    examples=self.examples(),
                    openmask_remote=ui_openmask.available(),
                    openmask_scenes=[dict(id=name, label=row["label"], query=ui_openmask.has_query(row))
                                     for name, row in ui_openmask.scene_catalog(self.root).items()],
                    has_ldraw=(find_ldraw(self.root) / "parts").is_dir(),
                    note="Rhino 文件虽带 RGB，本路径显式忽略颜色；Tiko 保留 RGB 并烘焙 UV 贴图")

    def examples(self) -> list[dict]:
        """列出本机已准备好的展示结果，使用普通运行记录的产物结构。"""
        rows = []
        for manifest in sorted(self.runs.glob("example_*/pipeline3d.json")):
            try:
                run = self._safe_run(str(manifest.parent))
                state = _read(manifest)
                example = state.get("example") or {}
                source = Path(state.get("source", "")).resolve()
                model = next((Path(a["path"]).resolve() for a in state.get("assets", []) if a.get("id") == "lego"), None)
                if (example.get("id") == run.name.removeprefix("example_") and example.get("label")
                        and state.get("flow") in FLOWS and source.is_file()
                        and model and model.is_relative_to(run) and model.is_file()):
                    rows.append(dict(example, flow=state["flow"]))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return rows

    def load_example(self, example_id: str) -> dict:
        """打开指定展示结果，沿用历史记录的选择与下载逻辑。"""
        example = next((row for row in self.examples() if row["id"] == example_id), None)
        if example is None:
            raise ValueError("该展示示例不可用，请选择其他示例或上传模型")
        return self.load(example["flow"], str(self.runs / f"example_{example_id}"))

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

    def upload_openmask(self, name: str, data: bytes, kind: str) -> dict:
        """Store a scene PLY or exported OpenMask3D mask array for instance selection."""
        suffixes = {"scene": {".ply"}, "instance": {".ply"}, "masks": {".npy", ".npz"}}
        suffix = Path(name).suffix.lower()
        if suffix not in suffixes.get(kind, ()) or not data or len(data) > 128 * 1024 ** 2:
            raise ValueError("场景需为 PLY，掩码需为 NPY 或 NPZ，每个文件不超过 128 MiB")
        if kind != "instance" and self.is_running():
            raise RuntimeError("3D 转换正在运行，请先停止")
        folder = self.root / "ui_uploads/openmask3d" / secrets.token_hex(8)
        folder.mkdir(parents=True)
        clean = re.sub(r"[^a-zA-Z0-9_.-]", "-", Path(name).stem).strip(".") or kind
        target = folder / (("source_" + clean if kind == "instance" else kind) + suffix)
        target.write_bytes(data)
        response = {"path": str(target)}
        if kind in {"scene", "instance"}:
            preview = folder / "scene_points.ply"
            state = self._call_openmask([str(target), "--preview", str(preview)])
            response.update(scene_points=state["scene_points"],
                            url="/file?" + urllib.parse.urlencode({"path": str(preview)}))
        return response

    def openmask_instances(self, scene: str, masks: str, instance: int | None = None,
                           point: int | None = None) -> dict:
        """Inspect uploaded masks or export a selected instance for the colour PLY input."""
        if self.is_running():
            raise RuntimeError("3D 转换正在运行，请先停止")
        inputs = (Path(scene).resolve(), Path(masks).resolve())
        folder = (self.root / "ui_uploads/openmask3d").resolve()
        if any(not path.is_relative_to(folder) or not path.is_file() for path in inputs):
            raise ValueError("请先上传场景 PLY 和对应的掩码")
        if inputs[0].suffix != ".ply" or inputs[1].suffix not in {".npy", ".npz"}:
            raise ValueError("场景需为 PLY，掩码需为 NPY 或 NPZ")
        arguments = [*map(str, inputs)]
        if instance is not None:
            output = folder / secrets.token_hex(8) / f"instance_{instance}.ply"
            arguments.extend(["--instance", str(instance), "--output", str(output)])
        if point is not None:
            arguments.extend(["--point", str(point)])
        state = self._call_openmask(arguments)
        if instance is not None:
            catalog = _read(inputs[0].parent / "dataset.json")
            if catalog and inputs[1] == inputs[0].with_name(catalog["masks"]):
                state.update(dataset=catalog["id"], scene_label=catalog["label"])
                _write(Path(state["path"]).with_suffix(".json"), state)
            state["url"] = "/file?" + urllib.parse.urlencode({"path": state["path"]})
        return state

    def load_openmask_scene(self, name: str) -> dict:
        """Load one server dataset and enumerate its saved masks without running segmentation."""
        row = ui_openmask.scene_catalog(self.root).get(name)
        if row is None:
            raise ValueError("请选择已登记的 OpenMask3D 场景")
        if self.is_running():
            raise RuntimeError("3D 转换正在运行，请先停止")
        if not self.openmask_lock.acquire(blocking=False):
            raise RuntimeError("已有 OpenMask3D 任务运行中")
        try:
            token = secrets.token_hex(8)
            folder = self.root / "ui_uploads/openmask3d" / token
            scene, masks = ui_openmask.stage_scene(row, folder, self.root / "segmentation_lab/openmask3d_worker.py")
            result = self.openmask_instances(str(scene), str(masks))
            preview = folder / "scene_points.ply"
            self._call_openmask([str(scene), "--preview", str(preview)])
            _write(folder / "dataset.json", dict(id=name, label=row["label"], masks=masks.name))
            return dict(result, path=str(scene), masks=str(masks), token=token, label=row["label"],
                        query=ui_openmask.has_query(row), url="/file?" + urllib.parse.urlencode({"path": str(preview)}))
        finally:
            self.openmask_lock.release()

    def query_openmask_scene(self, token: str, query: str, point: int | None = None) -> dict:
        """Rank the staged scene's existing masks by query text and optional selected point."""
        query = query.strip()
        if not re.fullmatch(r"[0-9a-f]{16}", token) or not 1 <= len(query) <= 160:
            raise ValueError("请载入场景并输入不超过 160 字符的检索词")
        folder = self.root / "ui_uploads/openmask3d" / token
        saved = _read(folder / "dataset.json")
        row = ui_openmask.scene_catalog(self.root).get(saved.get("id"))
        if row is None:
            raise ValueError("场景已失效，请重新载入")
        result = self.openmask_instances(str(folder / "scene.ply"), str(folder / saved["masks"]), point=point)
        if not self.openmask_lock.acquire(blocking=False):
            raise RuntimeError("已有 OpenMask3D 任务运行中")
        try:
            scores = ui_openmask.rank_instances(row, query, self.root / "segmentation_lab/openmask3d_worker.py")
        finally:
            self.openmask_lock.release()
        if len(scores) != result["mask_count"]:
            raise ValueError("实例特征行数与掩码列数不一致，请使用同一次分割保存的文件")
        ranked = [dict(entry, score=scores[entry["id"]]) for entry in result["instances"] if scores[entry["id"]] is not None]
        ranked.sort(key=lambda entry: (-entry["score"], entry["id"]))
        return dict(instances=ranked, query=query)

    def generate_openmask(self, scene: str, point: int) -> dict:
        """Run the configured mask proposal backend and return masks covering the picked point."""
        path = Path(scene).resolve()
        root = (self.root / "ui_uploads/openmask3d").resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.name != "scene.ply":
            raise ValueError("请先上传用于选点的场景 PLY")
        if self.is_running():
            raise RuntimeError("3D 转换正在运行，请先停止")
        if not self.openmask_lock.acquire(blocking=False):
            raise RuntimeError("已有 OpenMask3D 分割任务运行中")
        try:
            masks = path.with_name("generated_masks.npz")
            ui_openmask.generate_masks(path, masks, self.root / "segmentation_lab/openmask3d_worker.py")
            return dict(self.openmask_instances(str(path), str(masks), point=point), masks=str(masks))
        finally:
            self.openmask_lock.release()

    def _call_openmask(self, arguments: list[str]) -> dict:
        command = [self.python, "-m", "legolizer.processing3d.openmask_instances", *arguments]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.core_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run(command, cwd=self.core_root, env=env, timeout=120, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("读取掩码超时，请在服务器上先导出所选实例 PLY") from exc
        if result.returncode:
            raise ValueError(result.stderr.strip()[-1500:] or "读取 OpenMask3D 掩码失败")
        return json.loads(result.stdout.splitlines()[-1])

    def start(self, payload: dict) -> dict:
        flow = str(payload.get("flow", "stl"))
        scene_workflow = payload.get("workflow") == "scene"
        if scene_workflow and (flow != "ply_color" or not payload.get("source") or payload["source"] == "demo"):
            raise ValueError("请先导入分割出的彩色 PLY")
        if not isinstance(payload.get("options", {}), dict):
            raise ValueError("处理参数必须是对象")
        if payload.get("prepare_only") and payload.get("layout_only"):
            raise ValueError("请选择几何预览或 LEGO 布局预览")
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
            directory = self.runs / "scene" if scene_workflow else self.runs
            output = directory / (time.strftime("%Y%m%d_%H%M%S") + "_" + flow + "_" + secrets.token_hex(4))
            self.runs.mkdir(parents=True, exist_ok=True)
            if not scene_workflow:
                _write(self.runs / "selection.json", {flow: str(output), **{
                    k: v for k, v in _read(self.runs / "selection.json").items() if k != flow}})
            self.current = dict(flow=flow, output=str(output), source=str(source), options=options,
                                status="starting", started=time.time(), ended=None,
                                is_demo=source == self.demo(flow).resolve(),
                                prepare_only=bool(payload.get("prepare_only", False)),
                                layout_only=bool(payload.get("layout_only", False)), message="启动 3D 流水线")
            self.cancel.clear()
            self.logs.clear()
            self.worker = threading.Thread(target=self._run, daemon=True)
            # The worker takes this lock before launching the subprocess.
            self.worker.start()
            self.running = True
            return dict(output=str(output), flow=flow)

    def _run(self):
        job = self.current
        output = Path(job["output"])
        command = [self.python, "-m", "legolizer.processing3d.file_pipeline", "--source", job["source"],
                   "--output", str(output), "--flow", job["flow"], "--options", json.dumps(job["options"])]
        if job["prepare_only"]:
            command.append("--prepare-only")
        elif job.get("layout_only"):
            command.append("--layout-only")
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
                try:
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
                except (OSError, ValueError) as exc:
                    self.logs.append(str(exc))
                    job.update(status="failed", message="保存 3D 任务记录失败，请检查输出目录和日志")
                finally:
                    self.running, self.process = False, None

    def stop(self, output: str | None = None):
        with self.lock:
            if output and self.current.get("output") != str(self._safe_run(output)):
                return
            self.cancel.set()
            process = self.process
        if process is not None:
            terminate_process_tree(process)

    def load(self, flow: str, value: str) -> dict:
        with self.lock:
            if self.running:
                raise RuntimeError("任务运行时不能切换记录")
            run = self._safe_run(value)
            state = _read(run / "pipeline3d.json")
            if state.get("flow") != flow:
                raise ValueError("运行记录与当前转换路径不符")
            selections = _read(self.runs / "selection.json")
            selections[flow] = str(run)
            _write(self.runs / "selection.json", selections)
            self.current = {}
            self.logs.clear()
            return self.status(flow)

    def load_demo(self, flow: str) -> dict:
        demo_source = str(self.demo(flow).resolve())
        candidates = []
        for row in self.history(flow):
            state = _read(Path(row["output"]) / "pipeline3d.json")
            if state.get("source") != demo_source:
                continue
            report = state.get("report", {})
            if flow == "ply_color" and report.get("texture", {}).get("method") != "uvatlas-point-rgb":
                continue
            if flow != "stl" and POINT_CLOUD_MESH_PROFILE:
                if report.get("reconstruction", {}).get("profile") != POINT_CLOUD_MESH_PROFILE:
                    continue
                if not any(a.get("id") == "mesh_ply" and Path(a.get("path", "")).is_file()
                           for a in state.get("assets", [])):
                    continue
            candidates.append((bool(report.get("legolization", {}).get("model_path")), row.get("started") or 0, row["output"]))
        if candidates:
            return self.load(flow, max(candidates)[2])
        return self.start(dict(flow=flow, source="demo", prepare_only=True))

    def history(self, flow: str) -> list:
        result = []
        for path in sorted(self.runs.glob("*/pipeline3d.json"), reverse=True):
            try:
                data = _read(path)
                if data.get("flow") == flow:
                    result.append(dict(output=str(path.parent), status=data.get("status"), example=data.get("example"),
                                       source=Path(data.get("source", "")).name,
                                       started=data.get("started"), options=data.get("options", {})))
            except (OSError, ValueError):
                pass
        return result[:40]

    def status(self, flow: str = "stl", output: str | None = None) -> dict:
        if flow not in FLOWS:
            raise ValueError("无效的路径")
        with self.lock:
            job = dict(self.current) if self.current.get("flow") == flow else {}
            selections = _read(self.runs / "selection.json")
            if output:
                run = self._safe_run(output)
                if job and Path(job["output"]).resolve() != run:
                    job = {}
                if not job and _read(run / "pipeline3d.json").get("flow") != flow:
                    raise ValueError("运行记录与转换路径不符")
            else:
                if job and Path(job["output"]).parent == self.runs / "scene":
                    job = {}
                run = Path(job["output"]) if job else Path(selections[flow]) if selections.get(flow) else None
            if run is not None:
                run = self._safe_run(str(run))
                state = job if job.get("status") == "failed" else _read(run / "pipeline3d.json") or job
            else:
                state = dict(flow=flow, status="idle", assets=[], stages=[], message="载入默认示例或选择文件")
            state = dict(state)
            state["running"] = bool(self.running and job)
            state["busy"] = self.running
            state["active_job"] = None
            if self.running:
                active_output = self.current["output"]
                state["active_job"] = dict(output=active_output, flow=self.current["flow"],
                                           workflow="scene" if Path(active_output).parent == self.runs / "scene" else "3d")
            state["elapsed"] = round((state.get("ended") or time.time()) - state.get("started", time.time()), 1)
            state["logs"] = "\n".join(self.logs) if job else ((run / "run.log").read_text(encoding="utf-8", errors="replace")[-25000:] if run and (run / "run.log").is_file() else "")
            if state.get("status") in {"running", "starting"} and not state["running"]:
                state.update(status="interrupted", message="此前任务已中断，保留现有阶段产物")
            state["assets"] = [dict(asset, url="/file?" + urllib.parse.urlencode({"path": asset["path"]})) for asset in state.get("assets", [])
                               if Path(asset["path"]).is_file() and Path(asset["path"]).resolve().is_relative_to(self.runs.resolve())]
            if run and run.parent == self.runs / "scene" and not any(asset["id"] == "lego" for asset in state["assets"]):
                bundle_dir = run / "solver/legolization"
                model = (bundle_dir / "model/model.mpd").resolve()
                if model.is_relative_to(run) and model.is_file():
                    bundle_record = _read(bundle_dir / "bundle.json")
                    if bundle_record.get("stages", {}).get("model", {}).get("status") == "complete":
                        state["assets"].append(dict(id="lego", label="legolization LEGO", path=str(model),
                                                    kind="lego", stage="lego",
                                                    url="/file?" + urllib.parse.urlencode({"path": str(model)})))
                        if state["running"]:
                            state["message"] = "LEGO 模型已生成，可以预览和下载。正在处理装配说明。"
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
