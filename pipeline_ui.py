"""Dependency-free local web UI for the 2D -> Hunyuan -> UniTEX -> LEGO pipeline."""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ui_assistant
import ui_agent_jobs
import ui_revisions
import existing_runs
import proxe_integration as proxe

from project_config import load_project_config, venv_executable

ROOT = Path(__file__).resolve().parent
load_project_config(ROOT)
PIPELINE = ROOT / "pipeline_2d_to_lego.py"
LEGO_ROOT = ROOT / "legolization"
LEGO_PYTHON = venv_executable(LEGO_ROOT / ".venv", "python")
SCALE_RECOMMENDER = ROOT / "lego_scale_recommendation.py"
RECOMMENDATION_MIN_STUDS = 8
RECOMMENDATION_MAX_STUDS = 128
DEFAULT_IMAGE = ROOT / "models" / "chicken" / "7ken.png"
UPLOADS = ROOT / "ui_uploads"
STATIC_ROOT = ROOT / "web_assets"
LDRAW_ROOT = ROOT / "LDrawLibrary" / "ldraw"


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(name).name).strip("-.")
    return stem or "upload.png"


def default_run_dir(image: Path) -> Path:
    return ROOT / "runs" / f"{image.stem}-hunyuan-unitex"


def display_form_values(run_dir: Path | None, image: Path | None) -> tuple[str, str]:
    """Keep the original upload and top-level folder in the visible form."""
    if run_dir is None:
        return (str(image) if image else "", "")
    main_dir = ui_revisions.root_run_dir(run_dir, ROOT)
    source = image
    report = main_dir / "input" / "preprocess.json"
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
        recorded = Path(payload["source"]).resolve()
        if recorded.is_file():
            source = recorded
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return (str(source) if source else "", str(main_dir))


def within_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def bundle_comparison(run_dir: Path, studs: int, original: Path) -> dict[str, object] | None:
    """Expose the newest usable bundle report without changing any model files."""
    prefix = f"lego-{studs}-bundle"
    bundle_dirs: list[tuple[int, Path]] = []
    for directory in run_dir.glob(f"{prefix}*"):
        if not directory.is_dir():
            continue
        match = re.fullmatch(rf"{re.escape(prefix)}(?:-(\d+))?", directory.name)
        if match:
            bundle_dirs.append((int(match.group(1) or 1), directory))
    for _, directory in sorted(bundle_dirs, reverse=True):
        report_path = directory / "comparison" / "report.json"
        if not report_path.is_file():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict) or not isinstance(report.get("candidates"), list):
            continue
        winner = report.get("winner") if isinstance(report.get("winner"), dict) else {}
        options: list[dict[str, object]] = [
            {
                "label": "原版（当前卡片）",
                "path": str(original),
                "url": "/file?path=" + urllib.parse.quote(str(original)),
                "format": "mpd",
                "mtime_ns": original.stat().st_mtime_ns,
                "available": True,
                "original": True,
                "winner": False,
            }
        ]
        for candidate in report["candidates"]:
            if not isinstance(candidate, dict):
                continue
            strategy, seed, variant = (candidate.get(key) for key in ("strategy", "seed", "variant"))
            if (
                not isinstance(strategy, str)
                or not re.fullmatch(r"[a-z][a-z0-9-]*", strategy)
                or not isinstance(seed, int)
                or seed < 0
                or not isinstance(variant, str)
                or not re.fullmatch(r"[a-z][a-z0-9-]*", variant)
            ):
                continue
            key = f"{strategy}-s{seed}-{variant}"
            path = directory / "work" / "pending" / key / "model.ldr"
            available = path.is_file()
            metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
            options.append(
                {
                    "label": f"{strategy} · seed {seed} · {variant}",
                    "path": str(path) if available else "",
                    "url": "/file?path=" + urllib.parse.quote(str(path)) if available else "",
                    "format": "ldr",
                    "mtime_ns": path.stat().st_mtime_ns if available else 0,
                    "available": available,
                    "original": False,
                    "winner": all(candidate.get(field) == winner.get(field) for field in ("strategy", "seed", "variant")),
                    "status": candidate.get("status", ""),
                    "selection_objective": candidate.get("selection_objective"),
                    "cross_colour_error": candidate.get("cross_colour_error"),
                    "metrics": {field: metrics.get(field) for field in ("brick_count", "stable", "max_score", "maximin_capacity")},
                }
            )
        return {
            "bundle": directory.name,
            "report_mtime_ns": report_path.stat().st_mtime_ns,
            "reason": str(report.get("reason", "")),
            "options": options,
        }
    return None


def artifact_inventory(run_dir: Path | None) -> list[dict[str, object]]:
    if run_dir is None:
        return []
    definitions = (
        ("输入检查", "input/review.png", "image"),
        ("透明底输入", "input/foreground.png", "image"),
        ("几何轮廓检查", "hunyuan/qa/shape_qa_overlay.png", "image"),
        ("Hunyuan 无色模型", "hunyuan/mesh_shape_review_clean.glb", "model"),
        ("UniTEX 六视图", "unitex/mv_rgb.png", "image"),
        ("UniTEX 彩色模型", "unitex/textured_mesh.glb", "model"),
        ("方位校正模型", "oriented/mesh_upright.glb", "model"),
        ("LEGO 输入网格", "oriented/lego_input.ply", "model"),
    )
    result = []
    for label, relative, kind in definitions:
        path = run_dir / relative
        if path.is_file():
            item = {
                "label": label,
                "relative": relative,
                "kind": kind,
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "url": "/file?path=" + urllib.parse.quote(str(path)),
            }
            if kind == "model" and path.suffix.lower() in {".glb", ".ply"}:
                item["preview_format"] = path.suffix.lower().removeprefix(".")
            result.append(item)
    ldraw_available = (LDRAW_ROOT / "parts").is_dir() and (LDRAW_ROOT / "LDConfig.ldr").is_file()
    lego_models: list[tuple[int, Path]] = []
    for path in run_dir.glob("lego-*/model.mpd"):
        match = re.fullmatch(r"lego-(\d+)", path.parent.name)
        if match:
            lego_models.append((int(match.group(1)), path))
    for studs, path in sorted(lego_models):
        item = {
            "label": f"LEGO 模型（{studs} studs）",
            "studs": studs,
            "relative": path.relative_to(run_dir).as_posix(),
            "kind": "model",
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "url": "/file?path=" + urllib.parse.quote(str(path)),
        }
        if ldraw_available:
            item["preview_format"] = "mpd"
        comparison = bundle_comparison(run_dir, studs, path)
        if comparison is not None:
            item["comparison"] = {
                "bundle": comparison["bundle"],
                "report_mtime_ns": comparison["report_mtime_ns"],
            }
            item["comparison_url"] = "/compare?" + urllib.parse.urlencode(
                {"run_dir": str(run_dir), "studs": studs}
            )
        result.append(item)
    for path in sorted((run_dir / "ui_assistant" / "jobs").glob("*/bundle/model/model.mpd")):
        if not path.is_file():
            continue
        relative = path.relative_to(run_dir).as_posix()
        result.append({
            "label": "助手生成的 LEGO 模型",
            "relative": relative,
            "kind": "model",
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "url": "/file?path=" + urllib.parse.quote(str(path)),
            **({"preview_format": "mpd"} if ldraw_available else {}),
        })
    legacy = run_dir / "lego" / "model.mpd"
    lego_input = run_dir / "oriented" / "lego_input.ply"
    if legacy.is_file() and (
        not lego_input.is_file() or legacy.stat().st_mtime >= lego_input.stat().st_mtime
    ):
        item = {
            "label": "LEGO 模型（旧版输出目录）",
            "relative": "lego/model.mpd",
            "kind": "model",
            "size": legacy.stat().st_size,
            "mtime_ns": legacy.stat().st_mtime_ns,
            "url": "/file?path=" + urllib.parse.quote(str(legacy)),
        }
        if ldraw_available:
            item["preview_format"] = "mpd"
        result.append(item)
    return result


def artifact_groups(run_dir: Path | None) -> list[dict[str, object]]:
    if run_dir is None:
        return []
    parent = ui_revisions.branch_parent(run_dir, ROOT)
    branch_stage = ui_revisions.branch_start_stage(run_dir, ROOT)
    stage_order = {"input": 0, "geometry": 1, "texture": 2, "orientation": 3, "lego": 4}
    def artifact_stage(relative: str) -> str:
        if relative.startswith("input/"):
            return "input"
        if relative.startswith("hunyuan/"):
            return "geometry"
        if relative.startswith("unitex/"):
            return "texture"
        if relative.startswith("oriented/"):
            return "orientation"
        return "lego"
    old: dict[str, dict[str, object]] = {}
    if parent:
        lineage: list[Path] = []
        current_parent: Path | None = parent
        seen: set[Path] = set()
        while current_parent is not None and current_parent not in seen and len(lineage) < 32:
            lineage.append(current_parent)
            seen.add(current_parent)
            current_parent = ui_revisions.branch_parent(current_parent, ROOT)
        for ancestor in reversed(lineage):
            old.update({item["relative"]: item for item in artifact_inventory(ancestor)})
    current = {item["relative"]: item for item in artifact_inventory(run_dir)}
    groups = []
    for relative in dict.fromkeys([*old, *current]):
        previous, latest = old.get(relative), current.get(relative)
        record = ui_revisions.selected_pair(run_dir, relative, ROOT)
        # Accepted inputs are copied into a branch at runtime. The local copy
        # is the selected version, not another pending candidate.
        accepted_branch_input = (
            (branch_stage == "texture" and relative == "unitex/mv_rgb.png")
            or (branch_stage == "input" and relative == "input/foreground.png")
        )
        # Orientation and LEGO input rebuilt after a UniTEX edit are derived
        # intermediates. Show the fresh result directly; only an explicit
        # upload/assistant edit of either artifact needs a comparison.
        auto_accepted_intermediate = (
            branch_stage == "texture"
            and relative in {"oriented/mesh_upright.glb", "oriented/lego_input.ply"}
        )
        skip_generated_pair = accepted_branch_input or auto_accepted_intermediate
        if skip_generated_pair and latest and record and record.get("origin") == "generated":
            record = None
        if previous and latest and ui_revisions.stage_for(relative):
            if not skip_generated_pair:
                record = ui_revisions.register_generated_pair(
                    run_dir, relative,
                    ui_revisions.available_artifact(parent, relative, ROOT),
                    run_dir / relative, ROOT,
                )
        if record:
            template = latest or previous
            assert template is not None
            def version_item(path_text: str) -> dict[str, object]:
                path = Path(path_text)
                item = dict(template)
                item["url"] = "/file?path=" + urllib.parse.quote(str(path))
                item["size"] = path.stat().st_size
                item["mtime_ns"] = path.stat().st_mtime_ns
                item.pop("comparison", None)
                item.pop("comparison_url", None)
                return item
            groups.append({
                "relative": relative, "stage": ui_revisions.stage_for(relative),
                "original": version_item(str(record["original"])),
                "candidate": version_item(str(record["candidate"])),
                "choice": record["choice"], "stale": False,
            })
        else:
            groups.append({
                "relative": relative, "stage": ui_revisions.stage_for(relative),
                "original": latest or previous, "candidate": None, "choice": None,
                "stale": bool(previous and not latest and branch_stage is not None
                              and stage_order[artifact_stage(relative)] >= stage_order[branch_stage]),
            })
    edit_state = proxe.read_state(run_dir)
    edited = proxe.safe_run_file(run_dir, edit_state.get("latest_result"))
    history = edit_state.get("history")
    last_source = history[-1].get("source") if isinstance(history, list) and history and isinstance(history[-1], dict) else None
    original_model = proxe.safe_run_file(run_dir, last_source) or proxe.original_model(run_dir)
    if edited and original_model:
        relative = original_model.relative_to(run_dir).as_posix()
        for index, group in enumerate(groups):
            if group["relative"] != relative:
                continue
            original = group["original"]
            if original is None:
                break
            candidate = dict(original)
            candidate.update({
                "label": "AI 修改版 3D 模型",
                "url": "/file?path=" + urllib.parse.quote(str(edited)),
                "size": edited.stat().st_size,
                "mtime_ns": edited.stat().st_mtime_ns,
                "preview_format": "glb",
            })
            groups[index] = {
                "relative": relative, "stage": "orientation", "original": original,
                "candidate": candidate, "choice": (
                    "candidate" if edit_state.get("selected_model") == str(edited)
                    else "original" if edit_state.get("status") == "original" else None
                ),
                "stale": False, "ai_edit": True,
            }
            break
    return groups


def build_command(
    payload: dict[str, object], *, active_run_dir: Path | None = None
) -> tuple[list[str], Path, Path]:
    image_text = str(payload.get("image_path", "")).strip()
    if not image_text:
        raise ValueError("请先选择或填写输入图片")
    image = Path(image_text).expanduser().resolve()
    if not image.is_file():
        raise ValueError(f"找不到输入图片：{image}")
    run_text = str(payload.get("output_dir", "")).strip()
    run_dir = Path(run_text).expanduser().resolve() if run_text else default_run_dir(image).resolve()
    if not within_root(run_dir):
        raise ValueError(f"输出目录必须位于项目目录内：{ROOT}")

    # The form always shows the original input and top-level folder. Resume
    # an adopted internal branch only when those two visible values still
    # match; changing either field starts a new top-level run as usual.
    if active_run_dir is not None:
        active_run_dir = active_run_dir.resolve()
        if ui_revisions.branch_parent(active_run_dir, ROOT) is not None:
            display_image, display_dir = display_form_values(active_run_dir, image)
            if image == Path(display_image) and run_dir == Path(display_dir):
                run_dir = active_run_dir
                texture = ui_revisions.branch_settings(run_dir, ROOT)
                if texture is not None:
                    image = Path(texture["source_image"])
                else:
                    branch_image = ui_revisions.branch_source_image(run_dir, ROOT)
                    if branch_image is None:
                        raise ValueError("修改分支缺少已确认的输入图片")
                    image = branch_image

    command = [sys.executable, str(PIPELINE), str(image), "--output-dir", str(run_dir)]
    texture_branch = ui_revisions.branch_settings(run_dir, ROOT)
    if texture_branch is not None:
        if image != Path(texture_branch["source_image"]):
            raise ValueError("六视图分支必须使用其原始透明底图片")
        command.extend([
            "--preprocess-mode", "off",
            "--from-shape", texture_branch["from_shape"],
            "--unitex-six-view", texture_branch["six_view"],
            "--unitex-cache-run-dir", texture_branch["unitex_cache_run_dir"],
        ])
    if payload.get("accept_preprocess"):
        command.append("--accept-preprocess")
    if payload.get("accept_geometry"):
        command.append("--accept-geometry")
    if payload.get("accept_unitex"):
        command.append("--accept-unitex")
    if payload.get("build_lego"):
        command.append("--build-lego")
        if selected := proxe.selected_lego_input(run_dir):
            command.extend(["--lego-input", str(selected)])
    if payload.get("bundle"):
        command.append("--bundle")
    if payload.get("force"):
        command.append("--force")
    projection = str(payload.get("projection", "ltm"))
    if projection not in ("ltm", "direct"):
        raise ValueError("无效的 UniTEX 投影方式")
    command.extend(["--unitex-projection", projection])
    try:
        unitex_gpu = int(os.environ.get("UNITEX_GPU", "1"))
    except (TypeError, ValueError) as exc:
        raise ValueError("远端 GPU 编号必须是整数") from exc
    if unitex_gpu < 0:
        raise ValueError("远端 GPU 编号不能为负数")
    command.extend(["--unitex-gpu", str(unitex_gpu)])
    try:
        studs = int(payload.get("target_studs", 24))
    except (TypeError, ValueError) as exc:
        raise ValueError("积木宽度必须是整数") from exc
    if not 8 <= studs <= 128:
        raise ValueError("积木宽度建议设置为 8–128 Stud")
    command.extend(["--target-studs", str(studs)])
    return command, image, run_dir


class PipelineController:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.logs: deque[str] = deque(maxlen=2500)
        self.command: list[str] = []
        self.image: Path | None = DEFAULT_IMAGE if DEFAULT_IMAGE.is_file() else None
        self.run_dir: Path | None = default_run_dir(DEFAULT_IMAGE) if DEFAULT_IMAGE.is_file() else None
        self.returncode: int | None = None
        self.message = "就绪"
        self.started_at: float | None = None
        self.stop_requested = False
        self.last_output_at: float | None = None
        self.lego_started_at: float | None = None
        self.recommendation_job: Path | None = None
        self.recommendation_error: dict[Path, str] = {}

    def is_running(self) -> bool:
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def start(self, payload: dict[str, object]) -> None:
        with self.lock:
            active_run_dir = self.run_dir
        imported = (active_run_dir is not None and
                    (active_run_dir / "pipeline_state.json").is_file() and
                    json.loads((active_run_dir / "pipeline_state.json").read_text(encoding="utf-8")).get("pipeline") in {"direct-3d-import", "existing-run-import"})
        if imported:
            if not payload.get("build_lego"):
                raise ValueError("已上传 3D 模型，请勾选允许构建 LEGO")
            source = proxe.current_model(active_run_dir)
            if source is None:
                raise ValueError("当前没有可用的 GLB 模型")
            try:
                studs = int(payload.get("target_studs", 24))
            except (TypeError, ValueError) as exc:
                raise ValueError("积木宽度必须是整数") from exc
            if not 8 <= studs <= 128:
                raise ValueError("积木宽度建议设置为 8–128 Stud")
            run_dir = active_run_dir
            image = None
            command = [sys.executable, str(PIPELINE), "--from-textured", str(source),
                       "--output-dir", str(run_dir), "--build-lego", "--target-studs", str(studs)]
            if selected := proxe.selected_lego_input(run_dir):
                command.extend(["--lego-input", str(selected)])
            if payload.get("bundle"):
                command.append("--bundle")
        else:
            command, image, run_dir = build_command(payload, active_run_dir=active_run_dir)
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("流水线正在运行，请等待或先停止当前任务")
            self.logs.clear()
            self.command = command
            self.image = image
            self.run_dir = run_dir
            self.pipeline_run_dir = run_dir
            self.returncode = None
            self.message = "正在启动"
            self.started_at = time.time()
            self.stop_requested = False
            self.last_output_at = self.started_at
            self.lego_started_at = None
            worker = threading.Thread(target=self._run, daemon=True)
            worker.start()

    def set_uploaded_image(self, image: Path, output_dir: Path) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("流水线正在运行，暂时不能切换输入图片")
            self.image = image
            self.run_dir = output_dir
            self.pipeline_run_dir = None
            self.message = "图片已上传，等待运行"
            self.returncode = None
            self.started_at = None

    def import_existing(self, identifier: str) -> Path:
        if self.is_running() or AI_EDITOR.is_running():
            raise RuntimeError("当前任务正在运行，请等待完成")
        run_dir = existing_runs.copy_run(identifier, ROOT)
        model = proxe.original_model(run_dir)
        if model is None:
            raise ValueError("已有结果缺少可编辑的 GLB")
        (run_dir / "pipeline_state.json").write_text(json.dumps({
            "pipeline": "existing-run-import", "stage": "oriented_review",
            "textured": str(model), "source_run": identifier,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.lock:
            self.run_dir = run_dir
            self.pipeline_run_dir = run_dir
            foreground = run_dir / "input/foreground.png"
            self.image = foreground if foreground.is_file() else None
            self.message = "已加载队友的已有结果"
            self.returncode = 0
            self.started_at = None
        return run_dir

    def import_model(self, name: str, raw: bytes) -> tuple[Path, Path]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("主流水线正在运行，暂时不能切换模型")
        if Path(name).suffix.lower() != ".glb":
            raise ValueError("目前只支持上传 .glb 3D 模型")
        if len(raw) < 12 or raw[:4] != b"glTF":
            raise ValueError("文件不是有效的 GLB 3D 模型")
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time_ns() % 1_000_000):06d}"
        run_dir = ROOT / "runs" / f"imported-{safe_name(Path(name).stem)}-{stamp}"
        model = run_dir / "oriented" / "mesh_upright.glb"
        model.parent.mkdir(parents=True, exist_ok=False)
        model.write_bytes(raw)
        (run_dir / "pipeline_state.json").write_text(
            json.dumps(
                {
                    "pipeline": "direct-3d-import",
                    "stage": "oriented_review",
                    "textured": str(model),
                    "imported_name": safe_name(name),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with self.lock:
            self.run_dir = run_dir
            self.pipeline_run_dir = run_dir
            self.image = None
            self.message = "已导入 3D 模型"
            self.returncode = 0
            self.started_at = None
        return run_dir, model

    def select_generated_model(self) -> Path:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("主流水线正在运行，暂时不能切换模型")
            run_dir = self.pipeline_run_dir
        model = proxe.current_model(run_dir)
        if run_dir is None or model is None:
            raise ValueError("还没有刚才生成的 3D 模型，请先上传 GLB 或完成 2D→3D")
        with self.lock:
            self.run_dir = run_dir
            self.message = "已选择刚才生成的模型"
        return model

    def _run(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            process = subprocess.Popen(
                self.command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with self.lock:
                self.process = process
                stop_requested = self.stop_requested
                self.message = "正在停止" if stop_requested else "运行中"
            if stop_requested:
                self._terminate(process)
            assert process.stdout is not None
            for line in process.stdout:
                with self.lock:
                    self.logs.append(line.rstrip())
                    self.last_output_at = time.time()
                    if "[5/5] legolization build" in line:
                        self.lego_started_at = self.last_output_at
            code = process.wait()
            with self.lock:
                self.returncode = code
                self.message = (
                    "已停止"
                    if self.stop_requested
                    else "LEGO 不可构建"
                    if code == 2 and self.lego_started_at is not None
                    else "已到达验收点"
                    if code == 2
                    else "结果不确定或部分完成"
                    if code == 3
                    else "LEGO 精确求解超出限制"
                    if code == 4
                    else "运行完成"
                    if code == 0
                    else f"运行失败（退出码 {code}）"
                )
                self.process = None
                self.stop_requested = False
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.logs.append(f"UI 启动流水线失败：{exc}")
                self.returncode = -1
                self.message = "启动失败"
                self.process = None
                self.stop_requested = False

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.terminate()

    def stop(self) -> None:
        with self.lock:
            process = self.process
            starting = process is None and self.message == "正在启动"
            running = process is not None and process.poll() is None
            if not (starting or running):
                return
            self.stop_requested = True
            self.logs.append("已请求停止当前任务")
            self.message = "正在停止"
        if process is not None:
            self._terminate(process)

    def activate_branch(self, branch: Path, image: Path) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("流水线正在运行，不能切换到修改版")
            self.run_dir = branch
            self.image = image
            self.message = "已采用修改版，等待运行到下一验收点"
            self.returncode = None
            self.logs.clear()
            self.started_at = None

    def status(self) -> dict[str, object]:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            run_dir = self.run_dir
            now = time.time()
            logs_text = "\n".join(self.logs)
            notices: list[dict[str, str]] = []
            if "Solution may be inaccurate" in logs_text:
                notices.append(
                    {
                        "level": "warning",
                        "text": (
                            "求解器报告数值精度警告。它不等于无解，但当前稳定性判断的可信度较低。"
                        ),
                    }
                )
            if running and self.lego_started_at is not None:
                silent_for = now - (self.last_output_at or self.lego_started_at)
                if silent_for >= 600:
                    notices.append(
                        {
                            "level": "warning",
                            "text": (
                                f"LEGO 求解已连续 {int(silent_for // 60)} 分钟没有新输出。"
                                "这通常表示困难实例或修复搜索，不代表已证明无解；可考虑降低 Studs 或停止任务。"
                            ),
                        }
                    )
            if not running and self.returncode == 2 and self.lego_started_at is not None:
                notices.append(
                    {
                        "level": "error",
                        "text": "Legolization 已明确返回不可构建或物理检查失败。",
                    }
                )
            elif not running and self.returncode == 3:
                notices.append(
                    {
                        "level": "warning",
                        "text": "Legolization 只得到部分结果，或无法确定最终可构建性。",
                    }
                )
            elif not running and self.returncode == 4:
                notices.append(
                    {
                        "level": "warning",
                        "text": "精确放置搜索超过规模或时间限制，当前没有完整结果。",
                    }
                )
            data = {
                "running": running,
                "message": self.message,
                "returncode": self.returncode,
                "logs": logs_text,
                "command": subprocess.list2cmdline(self.command) if self.command else "",
                "image": str(self.image) if self.image else "",
                "run_dir": str(run_dir) if run_dir else "",
                "elapsed": round(time.time() - self.started_at, 1) if self.started_at else 0,
                "notices": notices,
            }
        display_image, display_dir = display_form_values(run_dir, self.image)
        data["display_image"] = display_image
        data["display_output_dir"] = display_dir
        data["artifacts"] = artifact_inventory(run_dir)
        data["artifact_groups"] = artifact_groups(run_dir)
        data["assistant_available"] = bool(os.environ.get("DASHSCOPE_API_KEY"))
        data["lego_recommendation"] = self.lego_recommendation(run_dir, running)
        state = run_dir / "pipeline_state.json" if run_dir else None
        if state and state.is_file():
            try:
                data["pipeline_state"] = json.loads(state.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data["pipeline_state"] = {}
        else:
            data["pipeline_state"] = {}
        return data

    def lego_recommendation(
        self,
        run_dir: Path | None,
        pipeline_running: bool,
    ) -> dict[str, object]:
        if run_dir is None:
            return {"status": "unavailable"}
        mesh = proxe.selected_lego_input(run_dir) or run_dir / "oriented" / "lego_input.ply"
        cache = run_dir / "oriented" / "lego_recommendation.json"
        if not mesh.is_file():
            return {"status": "unavailable"}
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if (
                payload.get("source_mtime_ns") == mesh.stat().st_mtime_ns
                and payload.get("minimum") == RECOMMENDATION_MIN_STUDS
                and payload.get("maximum") == RECOMMENDATION_MAX_STUDS
            ):
                return {"status": "ready", **payload}
        except (OSError, json.JSONDecodeError):
            pass
        with self.lock:
            if self.recommendation_job == mesh:
                return {"status": "calculating", "minimum": RECOMMENDATION_MIN_STUDS, "maximum": RECOMMENDATION_MAX_STUDS}
            error = self.recommendation_error.get(mesh)
            if error:
                return {"status": "error", "error": error}
            if pipeline_running:
                return {"status": "waiting"}
            self.recommendation_job = mesh
        threading.Thread(
            target=self._calculate_lego_recommendation,
            args=(mesh, cache),
            daemon=True,
        ).start()
        return {"status": "calculating", "minimum": RECOMMENDATION_MIN_STUDS, "maximum": RECOMMENDATION_MAX_STUDS}

    def _calculate_lego_recommendation(self, mesh: Path, cache: Path) -> None:
        try:
            result = subprocess.run(
                [
                    str(LEGO_PYTHON),
                    str(SCALE_RECOMMENDER),
                    str(mesh),
                    "--minimum",
                    str(RECOMMENDATION_MIN_STUDS),
                    "--maximum",
                    str(RECOMMENDATION_MAX_STUDS),
                ],
                cwd=LEGO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "未知错误"
                raise RuntimeError(detail[-500:])
            payload = json.loads(result.stdout)
            cache.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.lock:
                self.recommendation_error.pop(mesh, None)
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.recommendation_error[mesh] = str(exc)
        finally:
            with self.lock:
                if self.recommendation_job == mesh:
                    self.recommendation_job = None



class AIEditController:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.logs: deque[str] = deque(maxlen=1800)
        self.run_dir: Path | None = None
        self.edit_folder: Path | None = None
        self.source: Path | None = None
        self.instruction = ""
        self.message = "等待编辑"
        self.returncode: int | None = None
        self.started_at: float | None = None
        self.stop_requested = False
        self.active = False

    def is_running(self) -> bool:
        with self.lock:
            return self.active

    def start(self, run_dir: Path | None, payload: dict[str, object]) -> None:
        if run_dir is None or not run_dir.is_dir():
            raise ValueError("请先运行流水线并生成 3D 模型")
        source = proxe.current_model(run_dir)
        if source is None:
            raise ValueError("请先完成贴图与朝向校正，再进行 AI 编辑")
        instruction = str(payload.get("instruction", "")).strip()
        if not instruction:
            raise ValueError("请输入希望如何修改 3D 模型")
        install = proxe.installation(ROOT)
        if not install["available"]:
            raise ValueError("Prox-E 尚未配置：" + "、".join(install["missing"]))
        category = ""
        try:
            gpu = int(os.environ.get("PROXE_GPU", "1"))
        except (TypeError, ValueError) as exc:
            raise ValueError("GPU 编号必须是整数") from exc
        with self.lock:
            if self.active:
                raise RuntimeError("AI 编辑正在运行，请等待或先停止当前任务")
        edit_folder = proxe.create_edit_folder(run_dir)
        (edit_folder / "instruction.txt").write_text(instruction + "\n", encoding="utf-8")
        (edit_folder / "request.json").write_text(
            json.dumps(
                {
                    "source": str(source),
                    "category_override": proxe.clean_category(category) if category else "",
                    "gpu": gpu,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with self.lock:
            self.logs.clear()
            self.logs.append(f"输入模型：{source.name}")
            self.logs.append(f"修改要求：{instruction}")
            self.run_dir = run_dir
            self.edit_folder = edit_folder
            self.source = source
            self.instruction = instruction
            self.message = "正在启动 AI 编辑"
            self.returncode = None
            self.started_at = time.time()
            self.stop_requested = False
            self.active = True
        threading.Thread(
            target=self._run,
            args=(edit_folder, source, instruction, category, gpu),
            daemon=True,
        ).start()

    def resumable_edit(self, run_dir: Path | None) -> Path | None:
        if run_dir is None or not run_dir.is_dir():
            return None
        edits = run_dir / "ai_edit"
        if not edits.is_dir():
            return None
        state = proxe.read_state(run_dir)
        completed = {str(item.get("edit_plan", "")) for item in state.get("history", []) if isinstance(item, dict)}
        for folder in sorted(edits.glob("edit-*"), reverse=True):
            if not (folder / "instruction.txt").is_file() or not (folder / "request.json").is_file():
                continue
            if str(folder / "edit_plan.json") not in completed:
                return folder
        return None

    def resume(self, run_dir: Path | None) -> None:
        with self.lock:
            if self.active:
                raise RuntimeError("AI 编辑正在运行")
        folder = self.resumable_edit(run_dir)
        if folder is None or run_dir is None:
            raise ValueError("当前没有可续跑的编辑任务")
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        source = proxe.safe_run_file(run_dir, request.get("source"))
        if source is None or not source.is_file():
            raise ValueError("原始 GLB 已不存在，无法续跑")
        instruction = (folder / "instruction.txt").read_text(encoding="utf-8").strip()
        category = str(request.get("category_override", ""))
        gpu = int(request.get("gpu", 1))
        with self.lock:
            self.logs.clear()
            self.logs.append(f"从断点继续：{folder.name}")
            self.run_dir = run_dir
            self.edit_folder = folder
            self.source = source
            self.instruction = instruction
            self.message = "正在从断点继续"
            self.returncode = None
            self.started_at = time.time()
            self.stop_requested = False
            self.active = True
        threading.Thread(target=self._run, args=(folder, source, instruction, category, gpu), daemon=True).start()

    def _execute(self, command: list[str], cwd: Path, stage: str) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        with self.lock:
            if self.stop_requested:
                raise RuntimeError("编辑已停止")
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with self.lock:
            self.process = process
            should_stop = self.stop_requested
            self.message = "正在停止" if should_stop else "编辑中"
        if should_stop:
            process.terminate()
        assert process.stdout is not None
        for line in process.stdout:
            text = line.strip()
            if text:
                with self.lock:
                    self.logs.append(text[-2000:])
        code = process.wait()
        with self.lock:
            self.process = None
        if code != 0:
            raise RuntimeError(f"{stage}失败（退出码 {code}）；请查看上方具体错误")

    def _run(
        self, edit_folder: Path, source: Path, instruction: str,
        category_override: str, gpu: int,
    ) -> None:
        try:
            plan_path = edit_folder / "edit_plan.json"
            with self.lock:
                self.logs.append("阶段 1/5：看原模型四面截图，理解指令")
            plan_command, plan_cwd = proxe.build_plan_command(
                ROOT, source=source, instruction=instruction,
                plan_path=plan_path, category=category_override, gpu=gpu,
            )
            if plan_path.is_file():
                with self.lock:
                    self.logs.append("复用已完成的截图与编辑计划")
            else:
                self._execute(plan_command, plan_cwd, "截图与指令分析")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            category = str(plan["category"])
            route = str(plan["route"])
            with self.lock:
                self.logs.append(f"编辑路线：{route}；模型类别：{category}")
                self.logs.append("阶段 2/5：生成代理并检查修改范围")
            command, cwd = proxe.build_command(
                ROOT, source=source, output_folder=edit_folder / "proxe_output",
                instruction=instruction, category=category, gpu=gpu,
                edit_plan_path=plan_path, stop_after_proxy=route == "proxy_addition",
            )
            self._execute(command, cwd, "Prox-E 模型编辑")
            sample = edit_folder / "proxe_output" / category / source.stem
            if route == "proxy_addition":
                with self.lock:
                    self.logs.append("阶段 3/5：只把新部件接到原 GLB，保留原网格和贴图")
                compose_command, compose_cwd = proxe.build_compose_command(
                    ROOT, source=source, sample=sample, plan_path=plan_path, gpu=gpu,
                )
                self._execute(compose_command, compose_cwd, "新增部件合成")
            with self.lock:
                if self.stop_requested:
                    raise RuntimeError("编辑已停止")
            output = proxe.find_output(edit_folder)
            if output is None:
                raise RuntimeError("Prox-E 已结束，但没有找到可用的 GLB")
            if route != "proxy_addition" and plan.get("preserve_existing_texture"):
                with self.lock:
                    self.logs.append("保留外观：校准生成贴图的整体亮度和饱和度")
                preserved_output = output.with_name("output_preserved.glb")
                preserve_command, preserve_cwd = proxe.build_preserve_appearance_command(
                    ROOT, source=source, generated=output, sample=sample,
                    output=preserved_output, gpu=gpu,
                )
                self._execute(preserve_command, preserve_cwd, "生成贴图颜色校准")
                output = preserved_output
            with self.lock:
                self.logs.append("阶段 4/5：检查最终网格")
            quality = proxe.validate_mesh(output)
            if route == "proxy_addition":
                preservation = proxe.validate_exact_source_preservation(source, output)
            elif plan.get("preserve_existing_texture"):
                preservation = proxe.validate_appearance_preservation(source, output)
            else:
                preservation = {"appearance_change_requested": True}
            (edit_folder / "quality_report.json").write_text(
                json.dumps(
                    {"mesh": quality, "preservation": preservation},
                    ensure_ascii=False, indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            with self.lock:
                self.logs.append(
                    "网格验收通过："
                    f"{quality['vertices']} 顶点，{quality['faces']} 面，"
                    "无孔洞、无非流形边"
                )
                if route == "proxy_addition":
                    self.logs.append("原模型几何、UV 和贴图像素完全一致")
                elif plan.get("preserve_existing_texture"):
                    self.logs.append("生成网格未改动，贴图亮度与饱和度验收通过")
                self.logs.append("阶段 5/5：生成 LEGO 输入")
            output_strategy = route
            lego_input = edit_folder / "lego_input.ply"
            def convert(palette: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                    str(LEGO_PYTHON),
                    str(PIPELINE),
                    "--stage",
                    "convert",
                    "--glb",
                    str(output),
                    "--ply",
                    str(lego_input),
                    "--orient",
                    "keep",
                    "--colour",
                    "lit",
                    "--lego-palette",
                    palette,
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                    check=False,
                )

            conversion = convert("common")
            with self.lock:
                for line in conversion.stdout.splitlines():
                    if not line.lstrip().startswith("("):
                        self.logs.append(line[-2000:])
            if conversion.returncode != 0 or not lego_input.is_file():
                with self.lock:
                    self.logs.append("调色板转换不可用，保留模型原色重新转换")
                conversion = convert("off")
                with self.lock:
                    for line in conversion.stdout.splitlines():
                        if not line.lstrip().startswith("("):
                            self.logs.append(line[-2000:])
            if conversion.returncode != 0 or not lego_input.is_file():
                raise RuntimeError(
                    "编辑模型已生成，但转换 LEGO 输入失败："
                    + (conversion.stdout.strip()[-500:] or f"退出码 {conversion.returncode}")
                )
            run_dir = edit_folder.parent.parent
            state = proxe.read_state(run_dir)
            history = state.get("history")
            if not isinstance(history, list):
                history = []
            history.append(
                {
                    "instruction": self.instruction,
                    "source": str(self.source),
                    "output": str(output),
                    "lego_input": str(lego_input),
                    "preview": str(proxe.output_preview(output) or ""),
                    "output_strategy": output_strategy,
                    "edit_plan": str(plan_path),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
            state.update(
                {
                    "status": "review",
                    "latest_result": str(output),
                    "latest_lego_input": str(lego_input),
                    "latest_instruction": self.instruction,
                    "latest_output_strategy": output_strategy,
                    "latest_edit_plan": str(plan_path),
                    "history": history,
                }
            )
            proxe.write_state(run_dir, state)
            with self.lock:
                self.returncode = 0
                self.message = "编辑完成，等待验收"
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.logs.append(f"AI 编辑失败：{exc}")
                self.returncode = -1
                self.message = "已停止" if self.stop_requested else "编辑失败"
        finally:
            with self.lock:
                self.process = None
                self.stop_requested = False
                self.active = False

    def stop(self) -> None:
        with self.lock:
            process = self.process
            if not self.active:
                return
            self.stop_requested = True
            self.message = "正在停止"
            self.logs.append("已请求停止 AI 编辑")
        if process is not None and process.poll() is None:
            process.terminate()

    def accept(self, run_dir: Path | None) -> None:
        if run_dir is None:
            raise ValueError("当前没有流水线任务")
        if self.is_running():
            raise RuntimeError("AI 编辑仍在运行")
        state = proxe.read_state(run_dir)
        model = proxe.safe_run_file(run_dir, state.get("latest_result"))
        lego_input = proxe.safe_run_file(run_dir, state.get("latest_lego_input"))
        if model is None or lego_input is None:
            raise ValueError("当前没有可以采用的完整 AI 编辑结果")
        state.update(
            {
                "status": "accepted",
                "selected_model": str(model),
                "selected_lego_input": str(lego_input),
            }
        )
        proxe.write_state(run_dir, state)

    def reset(self, run_dir: Path | None) -> None:
        if run_dir is None:
            raise ValueError("当前没有流水线任务")
        if self.is_running():
            raise RuntimeError("AI 编辑仍在运行")
        state = proxe.read_state(run_dir)
        state.pop("selected_model", None)
        state.pop("selected_lego_input", None)
        state.pop("source_model", None)
        state["status"] = "original"
        proxe.write_state(run_dir, state)

    def status(self, run_dir: Path | None) -> dict[str, object]:
        install = proxe.installation(ROOT)
        state = proxe.read_state(run_dir)
        latest = proxe.safe_run_file(run_dir, state.get("latest_result")) if run_dir else None
        selected = proxe.safe_run_file(run_dir, state.get("selected_model")) if run_dir else None
        source = proxe.current_model(run_dir)
        resume_folder = self.resumable_edit(run_dir)
        with self.lock:
            running = self.active
            same_run = run_dir is not None and self.run_dir == run_dir
            message = self.message if same_run else str(state.get("status", "等待编辑"))
            # Keep status responses small enough for frequent browser polling.
            # The full in-memory log can grow very large during VoxHammer.
            logs = "\n".join(list(self.logs)[-400:]) if same_run else ""
            elapsed = round(time.time() - self.started_at, 1) if same_run and self.started_at else 0
        return {
            "configured": bool(install["available"]),
            "missing": install["missing"],
            "running": running and same_run,
            "can_resume": bool(resume_folder) and not running,
            "resume_folder": str(resume_folder) if resume_folder else "",
            "message": message,
            "logs": logs,
            "elapsed": elapsed,
            "source": str(source) if source else "",
            "source_url": "/file?path=" + urllib.parse.quote(str(source)) if source else "",
            "latest_result": str(latest) if latest else "",
            "latest_url": "/file?path=" + urllib.parse.quote(str(latest)) if latest else "",
            "latest_preview_url": (
                "/file?path=" + urllib.parse.quote(str(proxe.output_preview(latest)))
                if latest and proxe.output_preview(latest)
                else ""
            ),
            "selected": bool(selected),
            "adoption_status": str(state.get("status", "")),
            "selected_model": str(selected) if selected else "",
            "latest_instruction": str(state.get("latest_instruction", "")),
            "history": state.get("history", []),
        }


CONTROLLER = PipelineController()
AI_EDITOR = AIEditController()
ASSISTANT_JOBS = ui_agent_jobs.AgentJobManager(ROOT, venv_executable(LEGO_ROOT / ".venv", "legolization"), LEGO_ROOT, LDRAW_ROOT)


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>2D → 3D → LEGO</title>
<style>
:root{--bg:#f4f1e8;--panel:#fffdf7;--ink:#23251f;--muted:#6d7166;--line:#d9d4c7;--yellow:#f2c94c;--red:#c84b3a;--green:#397a58;--blue:#345995}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#ece8dc,#f8f6ef 55%,#e7ecdf);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei",sans-serif}
.shell{max-width:1380px;margin:auto;padding:28px}.hero{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:20px}.hero h1{font-size:32px;margin:0 0 5px;letter-spacing:-1px}.hero p{margin:0;color:var(--muted)}
.badge{padding:9px 14px;border:1px solid var(--line);border-radius:999px;background:var(--panel);font-weight:700}.grid{display:grid;grid-template-columns:420px 1fr;gap:18px}.panel{background:rgba(255,253,247,.96);border:1px solid var(--line);border-radius:18px;box-shadow:0 12px 35px rgba(45,42,34,.08);padding:20px}.panel h2{font-size:18px;margin:0 0 15px}
label{display:block;font-size:13px;font-weight:700;margin:13px 0 6px}input[type=text],input[type=number],select{width:100%;border:1px solid #cfc9bb;background:white;border-radius:10px;padding:10px 11px;font:inherit}.fileline{display:flex;gap:8px}.fileline input{min-width:0}.fileline button{white-space:nowrap}
.checks{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:15px 0}.check{display:flex;gap:8px;align-items:flex-start;background:#f3f0e7;border:1px solid #dfd9ca;border-radius:10px;padding:9px;font-size:13px}.check input{margin-top:2px}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:15px}button{border:0;border-radius:11px;padding:11px 14px;font:inherit;font-weight:750;cursor:pointer;background:#e6e1d4;color:var(--ink)}button.primary{background:var(--yellow)}button.danger{background:#f3d4cf;color:#852c20}button:hover{filter:brightness(.97)}button:disabled{opacity:.5;cursor:not-allowed}
.hint{color:var(--muted);font-size:12px;line-height:1.55}.recommendation{margin-top:8px;padding:10px 11px;border:1px solid #cbd8c7;border-radius:10px;background:#edf5e9;color:#31593d;font-size:13px;line-height:1.45}.recommendation button{padding:5px 9px;margin-left:7px;background:#d5e8cf}.steps{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;margin-bottom:16px}.step{font-size:12px;padding:9px 7px;border-radius:9px;text-align:center;background:#ece8dd;color:#73766c}.step.active{background:#dfeade;color:#285a3f;font-weight:750}.step.wait{background:#f4e5b2;color:#755b00;font-weight:750}.step.skipped{background:#e8e8e3;color:#777}.step-action{width:100%;border:1px solid #aec7ad;cursor:pointer;line-height:1.3}.step-action:hover{background:#dcebd9}.notices{display:grid;gap:8px;margin:10px 0}.notice{border-radius:10px;padding:10px 12px;font-size:13px;line-height:1.5}.notice.warning{background:#fff1c7;color:#715300;border:1px solid #e2c56c}.notice.error{background:#f7d7d2;color:#842f23;border:1px solid #dc9d94}
.log{height:310px;overflow:auto;white-space:pre-wrap;background:#20231f;color:#dce8d5;border-radius:13px;padding:14px;font:12px/1.5 Consolas,monospace}.meta{display:flex;flex-wrap:wrap;gap:8px;color:var(--muted);font-size:12px;margin:10px 0}.meta span{background:#f0ede5;padding:5px 8px;border-radius:7px}.artifacts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}.artifact{border:1px solid var(--line);border-radius:13px;overflow:hidden;background:white}.artifact img{display:block;width:100%;height:190px;object-fit:contain;background:repeating-conic-gradient(#eee 0 25%,#ddd 0 50%) 50%/20px 20px}.model-preview{height:190px;display:flex;align-items:center;justify-content:center;padding:8px;background:repeating-conic-gradient(#eee 0 25%,#ddd 0 50%) 50%/20px 20px;color:#5e625a;font-size:12px;text-align:center;touch-action:none}.model-preview canvas{display:block;width:100%;height:100%;cursor:grab}.model-preview canvas:active{cursor:grabbing}.artifact .info{padding:10px;display:flex;flex-direction:column;align-items:flex-start;gap:4px}.artifact b{display:block;font-size:13px}.artifact a{font-size:12px;color:var(--blue);text-decoration:none}.empty{color:var(--muted);padding:35px 10px;text-align:center;border:1px dashed var(--line);border-radius:12px}
.model-preview{position:relative}.preview-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;background:#ffffffb8}.model-preview-missing{height:190px;display:flex;align-items:center;justify-content:center;padding:8px;background:repeating-conic-gradient(#eee 0 25%,#ddd 0 50%) 50%/20px 20px;color:#5e625a;font-size:12px;text-align:center}.artifact .credit{display:block;color:var(--muted);font-size:10px;margin-top:4px}
.artifact-disclosure{grid-column:1/-1;border-top:1px solid var(--line);padding-top:10px}.artifact-disclosure summary{display:inline-flex;align-items:center;gap:7px;cursor:pointer;color:var(--blue);font-size:13px;font-weight:700;list-style:none}.artifact-disclosure summary::-webkit-details-marker{display:none}.artifact-disclosure summary::after{content:"⌄";font-size:17px;line-height:1;transition:transform .2s}.artifact-disclosure[open] summary::after{transform:rotate(180deg)}.artifact-disclosure .artifacts{margin-top:12px}
.artifact .title-link{display:block;color:var(--blue);font-size:13px;font-weight:700;text-decoration:underline;text-underline-offset:2px}
.assistant-launch{position:fixed;right:18px;bottom:18px;z-index:20;background:#293b38;color:white;box-shadow:0 8px 28px #0003}.assistant-panel{position:fixed;right:0;top:0;bottom:0;width:390px;max-width:100vw;z-index:30;background:#fbfaf6;border-left:1px solid var(--line);box-shadow:-12px 0 35px #0002;display:flex;flex-direction:column}.assistant-panel[hidden],.assistant-launch[hidden],.picker[hidden]{display:none}.assistant-head{display:flex;align-items:center;justify-content:space-between;padding:16px;border-bottom:1px solid var(--line)}.assistant-head h2{margin:0;font-size:17px}.assistant-head button{padding:7px 10px}.chat-history{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:10px}.chat-line{max-width:95%;padding:10px 12px;border-radius:12px;background:#ece9df;white-space:pre-wrap;line-height:1.5;font-size:13px}.chat-line.user{align-self:flex-end;background:#dceadc}.chat-compose{position:relative;border-top:1px solid var(--line);padding:12px}.chat-compose textarea{width:100%;min-height:78px;resize:vertical;border:1px solid var(--line);border-radius:11px;padding:10px;font:inherit}.chat-actions{display:flex;gap:8px;justify-content:space-between;margin-top:7px}.chat-actions button{font-size:12px;padding:8px 11px}.picker{position:absolute;left:12px;right:12px;bottom:115px;max-height:min(65vh,500px);overflow:auto;background:#292a29;color:#f7f7f5;border:1px solid #555;border-radius:15px;box-shadow:0 12px 30px #0005;padding:10px;z-index:2}.picker-title{font-size:12px;color:#aaa;padding:8px 10px 4px}.picker-option{display:flex;justify-content:space-between;align-items:center;width:100%;text-align:left;color:inherit;background:transparent;font-weight:500;padding:10px;border-radius:8px}.picker-option:hover,.picker-option[aria-checked=true]{background:#414541}.picker-option[aria-checked=true]::after{content:'✓'}.picker-divider{height:1px;background:#555;margin:8px 4px}.artifact-pair{grid-column:span 2;border:2px solid #b3c8b2;border-radius:16px;padding:10px;background:#edf4eb}.artifact-pair .pair-title{font-size:12px;color:#42644d;font-weight:750;margin:0 0 8px}.pair-cards{display:grid;grid-template-columns:1fr 1fr;gap:10px}.artifact.choice-card{cursor:pointer;border-color:#aec3ae}.artifact.choice-card:hover{box-shadow:0 0 0 2px #a8c6a7}.version-tag{display:inline-block;font-size:11px;color:#42644d;background:#e5f1e1;border-radius:6px;padding:3px 6px;margin-bottom:5px}.stale-tag{color:#765f21;background:#fff0bf}.choice-confirm{display:flex;gap:6px;align-items:center;margin-top:8px;font-size:12px;flex-wrap:wrap}.choice-confirm button{padding:5px 9px;font-size:12px}.artifact-actions{display:flex;align-items:center;flex-wrap:wrap;width:100%;min-width:0;gap:5px}.artifact-actions>a{white-space:nowrap}.artifact .upload-revision{flex:none;margin-left:auto;padding:3px 5px;font-size:10px;line-height:1.1;border-radius:6px;background:#e9e6db;white-space:nowrap}.assistant-open .shell{margin-right:390px;max-width:none}.assistant-open .grid{grid-template-columns:minmax(240px,300px) minmax(0,1fr)}
.artifact-actions{justify-content:space-between}.artifact .upload-revision{margin-left:0}
.artifact.choice-card{position:relative}.choice-confirm{position:absolute;inset:0;z-index:5;margin:0;padding:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;border-radius:12px;background:rgba(245,248,242,.76);-webkit-backdrop-filter:blur(6px) saturate(.75);backdrop-filter:blur(6px) saturate(.75);box-shadow:inset 0 0 0 1px rgba(70,110,78,.2);text-align:center;animation:choice-fog-in .18s ease-out}.choice-prompt{font-size:14px;font-weight:750;line-height:1.5;color:#233d2c;text-shadow:0 1px 3px #fff}.choice-options{display:flex;gap:9px;justify-content:center}.choice-options button{min-width:54px;padding:7px 13px;border-radius:999px;font-size:12px;box-shadow:0 2px 8px #263a2a1f}.choice-options [data-confirm=yes]{background:#397a58;color:white}.choice-options [data-confirm=no]{background:#fff;color:#47584b;border:1px solid #b9cabb}@keyframes choice-fog-in{from{opacity:0}to{opacity:1}}@media(prefers-reduced-motion:reduce){.choice-confirm{animation:none}}
.artifact{position:relative}.artifact.choice-card>.version-tag{position:absolute;top:10px;right:10px;z-index:2;margin:0;padding:5px 8px;border:1px solid #b8d8bb;border-radius:999px;background:rgba(230,246,231,.92);box-shadow:0 2px 8px #17342122;pointer-events:none}.artifact>.stale-tag{position:absolute;top:10px;right:10px;z-index:2;margin:0;padding:5px 8px;border:1px solid #e3c96f;border-radius:999px;color:#765f21;background:rgba(255,240,191,.94);box-shadow:0 2px 8px #43350c22;pointer-events:none;font-size:11px;line-height:normal}
@media(max-width:900px){.grid{grid-template-columns:1fr}.artifacts{grid-template-columns:1fr 1fr}.hero{align-items:start;flex-direction:column}}@media(max-width:560px){.shell{padding:14px}.artifacts,.checks{grid-template-columns:1fr}.steps{grid-template-columns:1fr 1fr}.hero h1{font-size:25px}}
@media(max-width:1100px){.assistant-open .shell{margin-right:min(390px,40vw)}.assistant-open .grid{grid-template-columns:minmax(200px,42%) minmax(0,1fr)}.assistant-panel{width:min(390px,40vw)}}@media(max-width:650px){.assistant-open .grid{grid-template-columns:1fr}}@media(max-width:560px){.artifact-pair{grid-column:1}.pair-cards{grid-template-columns:1fr}}
.upload-preview{display:flex;align-items:center;gap:10px;margin-top:9px;padding:8px;border:1px solid var(--line);border-radius:10px;background:#f7f5ee;font-size:12px}.upload-preview[hidden]{display:none}.upload-preview img{width:72px;height:72px;object-fit:contain}.existing-run{margin-top:10px;padding:10px;border:1px solid var(--line);border-radius:10px}.artifact-heading{display:flex;justify-content:space-between;align-items:center;gap:10px}.artifact-heading button{font-size:12px;padding:7px 10px}
</style></head>
<body><div class="shell">
<div class="hero"><div><h1>2D → Hunyuan → UniTEX → LEGO</h1><p>本地实验控制台 · 阶段验收 · 断点续跑</p></div><div id="badge" class="badge">就绪</div></div>
<div class="grid"><section class="panel"><h2>输入与运行设置</h2>
<label>输入图片</label><div class="fileline"><input id="imagePath" type="text" value="__DEFAULT_IMAGE__" placeholder="上传图片，或填写服务器上的绝对路径"><button onclick="document.getElementById('file').click()">上传图片</button></div><input id="file" type="file" accept=".png,.jpg,.jpeg,.webp" hidden onchange="uploadFile(this)"><div id="inputUploadPreview" class="upload-preview" hidden><img id="inputPreview" alt="上传的输入图片"><span id="uploadStatus"></span></div><p class="hint">Mac 本机路径不能直接填在这里；使用“上传图片”后会自动写入服务器路径。</p>
<label>输出目录（留空自动生成）</label><input id="outputDir" type="text" placeholder="留空使用项目目录下的 runs 文件夹"><div id="existingRunBox" class="existing-run" hidden><label>直接加载队友已有结果</label><div class="fileline"><select id="existingRun"></select><button type="button" onclick="importExistingRun()">加载结果</button></div><p class="hint">加载中间产物后可以直接预览、修改 3D 模型，无需重新生成。</p></div>
<div class="checks">
<label class="check"><input id="acceptPre" type="checkbox"><span>已检查抠图<br><small>允许进入 Hunyuan</small></span></label>
<label class="check"><input id="acceptGeo" type="checkbox"><span>已检查几何<br><small>允许进入 UniTEX</small></span></label>
<label class="check"><input id="acceptTex" type="checkbox"><span>已检查六视图<br><small>允许贴图和校正</small></span></label>
<label class="check"><input id="buildLego" type="checkbox"><span>允许构建 LEGO<br><small>最终批准</small></span></label>
</div>
<label>UniTEX 投影</label><select id="projection"><option value="ltm">LTM（推荐，补全不可见面）</option><option value="direct">Direct（快速诊断）</option></select>

<label>LEGO 宽度（Stud）</label><input id="studs" type="number" min="8" max="128" value="24">
<div id="legoRecommendation" class="recommendation" hidden></div>
<div class="checks"><label class="check"><input id="bundle" type="checkbox"><span>生成完整 Bundle</span></label><label class="check"><input id="force" type="checkbox"><span>强制重跑已有阶段</span></label></div>
<div class="actions"><button class="primary" id="runButton" onclick="startRun()">运行到下一验收点</button><button class="danger" onclick="stopRun()">停止当前任务</button></div>
<p class="hint">透明底标准图会自动跳过抠图。没有勾选的验收项会成为本次运行的停止点。旧结果会自动复用；一般不要勾选“强制重跑”。</p>
</section>
<section class="panel"><div class="steps" id="steps"><div class="step">输入</div><div class="step">Hunyuan 几何</div><div class="step">UniTEX 六视图</div><div class="step">贴图与校正</div><button type="button" class="step step-action" onclick="open3DEditStep()">编辑 3D 模型</button><div class="step">LEGO</div></div>
<div class="meta"><span id="runDir">尚未运行</span><span id="elapsed">0 秒</span></div><div id="notices" class="notices"></div><div id="log" class="log">等待任务……</div>
<div class="artifact-heading"><h2 style="margin-top:18px">产物与预览</h2><button type="button" onclick="$('modelFile').click()">上传自己的 GLB</button><input id="modelFile" type="file" accept=".glb,model/gltf-binary" hidden onchange="upload3D(this)"></div><div id="artifacts" class="empty">运行后会在这里显示检查图和模型下载链接</div>
</section></div></div>
<button id="assistantLaunch" class="assistant-launch" onclick="toggleAssistant(true)">打开阶段助手</button>
<aside id="assistantPanel" class="assistant-panel" hidden aria-label="阶段助手">
  <div class="assistant-head"><h2>阶段助手</h2><button onclick="toggleAssistant(false)" aria-label="收起助手">收起 ×</button></div>
  <div id="chatHistory" class="chat-history"><div class="chat-line">选择阶段或对话模型，然后描述想检查或修改的内容。修改会先生成候选，原版不会覆盖。</div></div>
  <div class="chat-compose"><textarea id="chatInput" placeholder="描述你想调整的地方……"></textarea>
    <div id="modelPicker" class="picker" role="menu" hidden>
      <div class="picker-title">按阶段 · 自动选择对话模型</div>
      <button class="picker-option" role="menuitemradio" data-mode="stage" data-value="input" onclick="selectAssistant(this)">输入图片与抠图</button>
      <button class="picker-option" role="menuitemradio" data-mode="stage" data-value="geometry" onclick="selectAssistant(this)">3D 几何讨论</button>
      <button class="picker-option" role="menuitemradio" data-mode="stage" data-value="geometry_edit" onclick="selectAssistant(this)">修改 3D 模型</button>
      <button class="picker-option" role="menuitemradio" data-mode="stage" data-value="texture" onclick="selectAssistant(this)">六视图与着色</button>
      <button class="picker-option" role="menuitemradio" data-mode="stage" data-value="orientation" onclick="selectAssistant(this)">方位校正</button>
      <button class="picker-option" role="menuitemradio" data-mode="stage" data-value="lego" onclick="selectAssistant(this)">LEGO 拼搭</button>
      <div class="picker-divider"></div><div class="picker-title">直接选对话模型 · 不指定阶段</div>
      <button class="picker-option" role="menuitemradio" data-mode="model" data-value="qwen3.8-max" onclick="selectAssistant(this)">Qwen3.8 Max</button>
      <button class="picker-option" role="menuitemradio" data-mode="model" data-value="qwen3.8-flash" onclick="selectAssistant(this)">Qwen3.8 Flash</button>
    </div>
    <div class="chat-actions"><button id="selectionButton" onclick="togglePicker()" aria-haspopup="menu" aria-expanded="false">Qwen3.8 Max ▾</button><button id="stopAssistantJob" type="button" hidden onclick="stopAssistantJob()">停止任务</button><button id="resume3D" type="button" hidden onclick="resume3DResult()">继续编辑</button><button id="stop3D" type="button" hidden onclick="stop3DResult()">停止编辑</button><button id="sendChat" class="primary" onclick="sendChat()">发送 ↑</button></div>
  </div>
</aside><input id="revisionFile" type="file" hidden onchange="uploadRevision(this)">
<script type="importmap">{"imports":{"three":"/static/three/three.module.js","three/addons/":"/static/three/addons/"}}</script>
<script type="module" src="/static/model-preview.js"></script>
<script>
const $=id=>document.getElementById(id); let polling=false, artifactSignature=null, currentRunDir='', formInitialized=false, assistantSelection={mode:'model',value:'qwen3.8-max'}, chatTurns=[], pendingChoice=null, revisionRelative='', pointerStart=null, assistantJobId='', assistantJobLine=null;
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)throw new Error(d.error||'请求失败');return d}
function showInputPreview(url,text){$('inputPreview').src=url;$('uploadStatus').textContent=text;$('inputUploadPreview').hidden=false}
async function uploadFile(input){const f=input.files[0];if(!f)return;try{showInputPreview(URL.createObjectURL(f),'正在上传 '+f.name+'…');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('无法读取所选图片'));reader.readAsDataURL(f)});const d=await api('/api/upload',{name:f.name,data});$('imagePath').value=d.path;$('outputDir').value=d.output_dir;showInputPreview(d.url,'已上传 '+f.name+'，可以开始运行');formInitialized=true;artifactSignature=null;refresh()}catch(e){$('uploadStatus').textContent='上传失败：'+e.message;alert('图片上传失败：'+e.message)}finally{input.value=''}}
async function loadExistingRunOptions(){try{const r=await fetch('/api/examples');const d=await r.json();const runs=d.runs||[];$('existingRunBox').hidden=!runs.length;$('existingRun').innerHTML=runs.map(x=>`<option value="${esc(x.id)}">${esc(x.label)}</option>`).join('')}catch(e){$('existingRunBox').hidden=true}}
async function importExistingRun(){try{const id=$('existingRun').value;if(!id)throw new Error('请选择已有结果');const d=await api('/api/import-run',{id});currentRunDir=d.run_dir;formInitialized=false;artifactSignature=null;await refresh();chatLine('assistant','已加载队友的已有结果，可以直接预览和修改 3D 模型。')}catch(e){alert('加载已有结果失败：'+e.message)}}
async function startRun(){try{await api('/api/start',{image_path:$('imagePath').value,output_dir:$('outputDir').value,accept_preprocess:$('acceptPre').checked,accept_geometry:$('acceptGeo').checked,accept_unitex:$('acceptTex').checked,build_lego:$('buildLego').checked,bundle:$('bundle').checked,force:$('force').checked,projection:$('projection').value,target_studs:$('studs').value});polling=true;refresh()}catch(e){alert(e.message)}}
async function stopRun(){try{await api('/api/stop',{})}catch(e){alert(e.message)}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function stageIndex(state,arts,edit){const s=state.stage||'';if(s==='complete')return 6;if(edit?.selected)return 5;if(edit?.running||edit?.source||s==='oriented_review')return 4;if(s==='foreground_review')return 0;if(s==='geometry_review')return 1;if(s==='unitex_review')return 2;if(arts.some(a=>a.relative==='oriented/mesh_upright.glb'||a.relative==='unitex/textured_mesh.glb'))return 4;if(arts.some(a=>a.relative.includes('hunyuan/')))return 1;return 0}
function renderSteps(state,arts,edit){const idx=stageIndex(state,arts,edit);const nodes=[...$('steps').children];nodes.forEach((node,i)=>{node.className='step '+(i===4?'step-action ':'')+(i<idx?'active':i===idx?'wait':'');if(i===4){node.textContent=edit.running?'编辑 3D 中…':edit.latest_result&&!edit.selected&&edit.adoption_status==='review'?'编辑结果待采用':'编辑 3D 模型';if(state.stage==='complete'&&!edit.latest_result)node.className='step step-action skipped'}})}
function open3DEditStep(){toggleAssistant(true);selectAssistant(document.querySelector('.picker-option[data-value="geometry_edit"]'));$('chatInput').focus()}
function showRecommendation(r){const box=$('legoRecommendation');if(!r||r.status==='unavailable'){box.hidden=true;return}box.hidden=false;if(r.status==='ready'){box.innerHTML=`Legolization 推荐：<b>${r.target_studs} Stud</b><button type="button" onclick="$('studs').value=${r.target_studs}">使用推荐值</button><br><small>在 ${r.minimum}–${r.maximum} Stud 范围内按模型体素化质量自动选择；不保证最终一定可构建。</small>`}else if(r.status==='calculating'){box.textContent=`正在计算 Legolization 推荐宽度（${r.minimum}–${r.maximum} Stud）……`}else if(r.status==='waiting'){box.textContent='当前任务完成后计算 Legolization 推荐宽度……'}else{box.textContent='推荐宽度计算失败：'+(r.error||'未知错误')}}
const intermediatePaths=new Set(['input/review.png','hunyuan/qa/shape_qa_overlay.png','unitex/mv_rgb.png','oriented/lego_input.ply']);
function renderArtifacts(groups){
  const signature=JSON.stringify(groups.map(g=>[g.relative,g.original?.mtime_ns,g.candidate?.mtime_ns,g.choice,g.stale,g.original?.comparison?.bundle]));
  if(signature===artifactSignature)return;
  const expanded=$('intermediateArtifacts')?.open||false;
  window.disposeModelPreviews?.();
  artifactSignature=signature;
  const visual=a=>a.kind==='image'
    ?`<img src="${esc(a.url)}&v=${a.mtime_ns}" alt="${esc(a.label)}">`
    :a.preview_format
      ?`<div class="model-preview" data-model-format="${esc(a.preview_format)}" data-model-url="${esc(a.url)}&v=${a.mtime_ns}">等待加载 3D 预览…</div>`
      :a.relative.endsWith('.mpd')
        ?'<div class="model-preview-missing">未找到本机 LDraw 零件库；仍可下载 MPD</div>'
        :'';
  const artifactPath=a=>new URL(a.url,location.href).searchParams.get('path')||a.relative;
  const card=(a,g,version,selectable=false)=>`<div class="artifact ${selectable?'choice-card':''}" ${selectable?`data-relative="${esc(g.relative)}" data-version="${version}" data-ai-edit="${g.ai_edit?'1':'0'}"`:''}>${visual(a)}${selectable?`<span class="version-tag">${version==='original'?'原版':'修改版'}</span>`:''}${g.stale?'<span class="stale-tag">旧结果</span>':''}<div class="info">${a.comparison_url?`<a class="title-link" href="${esc(a.comparison_url)}" target="_blank" rel="noopener" title="${esc(artifactPath(a))}">${esc(a.label)}</a>`:`<b title="${esc(artifactPath(a))}">${esc(a.label)}</b>`}${a.preview_format==='mpd'?'<span class="credit">零件几何：<a href="https://www.ldraw.org/" target="_blank" rel="noopener">LDraw Parts Library</a></span>':''}<div class="artifact-actions"><a href="${esc(a.url)}" target="_blank" rel="noopener" title="打开 / 下载 · ${(a.size/1048576).toFixed(2)} MB">打开 / 下载 · ${(a.size/1048576).toFixed(2)} MB</a>${g.ai_edit&&g.choice==='candidate'?`<button type="button" data-ai-reset="1">恢复原模型</button>`:''}${g.stage&&!g.stale&&!g.ai_edit?`<button class="upload-revision" type="button" data-upload="${esc(g.relative)}">上传修改版</button>`:''}</div>${selectable&&pendingChoice?.relative===g.relative&&pendingChoice?.version===version?`<div class="choice-confirm" role="group" aria-label="确认选择这版卡片"><div class="choice-prompt">确认选这版吗？</div><div class="choice-options"><button data-confirm="yes">是</button><button data-confirm="no">否</button></div></div>`:''}</div></div>`;
  const group=g=>g.candidate&&!g.choice?`<div class="artifact-pair"><div class="pair-title">${esc(g.original.label)} · 选择一版</div><div class="pair-cards">${card(g.original,g,'original',true)}${card(g.candidate,g,'candidate',true)}</div></div>`:card(g.choice==='candidate'?g.candidate:g.original,g,g.choice||'',false);
  const main=groups.filter(g=>!intermediatePaths.has(g.relative));
  const intermediate=groups.filter(g=>intermediatePaths.has(g.relative));
  $('artifacts').className=groups.length?'artifacts':'empty';
  $('artifacts').innerHTML=groups.length
    ?main.map(group).join('')+(intermediate.length?`<details id="intermediateArtifacts" class="artifact-disclosure" ${expanded?'open':''}><summary>查看中间产物</summary><div class="artifacts">${intermediate.map(group).join('')}</div></details>`:'')
    :'运行后会在这里显示检查图和模型下载链接';
  window.mountModelPreviews?.();
}
function toggleAssistant(open){$('assistantPanel').hidden=!open;$('assistantLaunch').hidden=open;document.body.classList.toggle('assistant-open',open)}
function togglePicker(){const p=$('modelPicker');p.hidden=!p.hidden;$('selectionButton').setAttribute('aria-expanded',String(!p.hidden))}
function selectAssistant(button){assistantSelection={mode:button.dataset.mode,value:button.dataset.value};$('selectionButton').textContent=button.textContent.trim()+' ▾';$('modelPicker').hidden=true;$('selectionButton').setAttribute('aria-expanded','false');document.querySelectorAll('.picker-option').forEach(b=>b.setAttribute('aria-checked',String(b===button)))}
document.querySelector('.picker-option[data-value="qwen3.8-max"]').setAttribute('aria-checked','true');
function chatLine(role,text){const el=document.createElement('div');el.className='chat-line '+role;el.textContent=text;$('chatHistory').append(el);$('chatHistory').scrollTop=$('chatHistory').scrollHeight;return el}
async function sendChat(){const message=$('chatInput').value.trim();if(!message)return;$('chatInput').value='';chatLine('user',message);const wait=chatLine('assistant','思考中……');$('sendChat').disabled=true;try{if(assistantSelection.value==='geometry_edit'){await api('/api/ai-edit/start',{instruction:message});wait.textContent='已提交 3D 模型修改，正在分析模型与指令……';poll3DEdit(wait);return}const d=await api('/api/assistant/chat',{run_dir:currentRunDir,mode:assistantSelection.mode,value:assistantSelection.value,message,history:chatTurns});wait.textContent=d.answer;chatTurns.push({role:'user',content:message},{role:'assistant',content:d.answer});if(d.job){assistantJobId=d.job.id;assistantJobLine=chatLine('assistant','任务已排队……');$('stopAssistantJob').hidden=false;pollAssistantJob()}}catch(e){wait.textContent=e.message}finally{$('sendChat').disabled=false}}
async function upload3D(input){const file=input.files[0];if(!file)return;try{if(!file.name.toLowerCase().endsWith('.glb'))throw new Error('请上传 GLB 文件');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file)});const d=await api('/api/ai-edit/upload',{name:file.name,data});currentRunDir=d.run_dir;formInitialized=false;artifactSignature=null;await refresh();chatLine('assistant','3D 模型已加载。在右侧阶段助手选择“修改 3D 模型”，然后输入修改要求。')}catch(e){alert('上传 GLB 失败：'+e.message)}finally{input.value=''}}
async function accept3DResult(){try{await api('/api/ai-edit/accept',{});chatLine('assistant','已采用最新模型，后续乐高化将使用它。');artifactSignature=null;refresh()}catch(e){chatLine('assistant',e.message)}}
async function reset3DResult(){try{await api('/api/ai-edit/reset',{});artifactSignature=null;refresh();chatLine('assistant','已恢复原模型。')}catch(e){chatLine('assistant',e.message)}}
async function stop3DResult(){try{await api('/api/ai-edit/stop',{});refresh()}catch(e){chatLine('assistant',e.message)}}
async function resume3DResult(){try{await api('/api/ai-edit/resume',{});chatLine('assistant','已从上次中断处继续。');refresh()}catch(e){chatLine('assistant',e.message)}}
async function poll3DEdit(line){try{const r=await fetch('/api/status');const d=await r.json();const edit=d.ai_editor||{};line.textContent=edit.message||'编辑中';$('stop3D').hidden=!edit.running;if(edit.running){setTimeout(()=>poll3DEdit(line),2000);return}if(edit.latest_result){line.textContent+=' · 修改版已放到中间模型卡片，请选择原版或修改版。';artifactSignature=null}refresh()}catch(e){line.textContent='编辑状态读取失败：'+e.message}}
async function pollAssistantJob(){if(!assistantJobId)return;try{const r=await fetch('/api/assistant/job');const d=await r.json();if(d.id!==assistantJobId)return;if(assistantJobLine)assistantJobLine.textContent=d.summary||d.state;if(['queued','running'].includes(d.state)){setTimeout(pollAssistantJob,2000)}else{$('stopAssistantJob').hidden=true;assistantJobId='';artifactSignature=null;refresh();if(d.result_path&&assistantJobLine){const a=document.createElement('a');a.href='/file?path='+encodeURIComponent(d.result_path);a.target='_blank';a.rel='noopener';a.textContent=' 打开结果';assistantJobLine.append(a)}}}catch(e){if(assistantJobLine)assistantJobLine.textContent='任务状态读取失败：'+e.message;$('stopAssistantJob').hidden=true;assistantJobId=''}}
async function stopAssistantJob(){try{await api('/api/assistant/stop',{})}catch(e){alert(e.message)}}
function openRevisionUpload(relative){revisionRelative=relative;const f=$('revisionFile');f.accept='.'+relative.split('.').pop();f.value='';f.click()}
async function uploadRevision(input){const f=input.files[0];if(!f)return;try{const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(f)});await api('/api/revision/upload',{run_dir:currentRunDir,relative:revisionRelative,name:f.name,data});artifactSignature=null;refresh()}catch(e){alert(e.message)}}
$('artifacts').addEventListener('pointerdown',e=>{pointerStart={x:e.clientX,y:e.clientY}});
$('artifacts').addEventListener('click',async e=>{const aiReset=e.target.closest('[data-ai-reset]');if(aiReset){await reset3DResult();return}const upload=e.target.closest('[data-upload]');if(upload){openRevisionUpload(upload.dataset.upload);return}const confirm=e.target.closest('[data-confirm]');if(confirm){const choice=pendingChoice;if(!choice)return;if(confirm.dataset.confirm==='no'){pendingChoice=null;artifactSignature=null;refresh();return}try{if(choice.ai_edit){await api(choice.version==='candidate'?'/api/ai-edit/accept':'/api/ai-edit/reset',{})}else{await api('/api/revision/choose',{run_dir:currentRunDir,relative:choice.relative,choice:choice.version})}pendingChoice=null;artifactSignature=null;refresh()}catch(err){alert(err.message)}return}if(e.target.closest('a,button'))return;const card=e.target.closest('.choice-card');if(!card)return;if(pointerStart&&Math.hypot(e.clientX-pointerStart.x,e.clientY-pointerStart.y)>6)return;pendingChoice={relative:card.dataset.relative,version:card.dataset.version,ai_edit:card.dataset.aiEdit==='1'};artifactSignature=null;refresh()});
async function refresh(){try{const r=await fetch('/api/status');const d=await r.json();currentRunDir=d.run_dir||'';const edit=d.ai_editor||{};$("resume3D").hidden=!edit.can_resume;$("stop3D").hidden=!edit.running;if(!formInitialized){if(d.display_image)$('imagePath').value=d.display_image;if(d.display_output_dir)$('outputDir').value=d.display_output_dir;if(d.display_image&&d.display_image.includes('/ui_uploads/'))showInputPreview('/file?path='+encodeURIComponent(d.display_image),'已上传的输入图片');formInitialized=true}$('badge').textContent=(edit.running?edit.message:d.message)+((d.running||edit.running)?' · '+(edit.running?edit.elapsed:d.elapsed)+'s':'');$('runButton').disabled=d.running||edit.running;showRecommendation(d.lego_recommendation);const notices=d.notices||[];$('notices').innerHTML=notices.map(n=>`<div class="notice ${n.level==='error'?'error':'warning'}">${esc(n.text)}</div>`).join('');$('log').textContent=(edit.running||edit.logs?edit.logs:d.logs)||'等待任务……';$('log').scrollTop=$('log').scrollHeight;$('runDir').textContent=d.display_output_dir||'尚未运行';$('elapsed').textContent=d.elapsed+' 秒';renderSteps(d.pipeline_state||{},d.artifacts||[],edit);renderArtifacts(d.artifact_groups||[]);polling=!!(d.running||edit.running);if(polling)setTimeout(refresh,1200)}catch(e){polling=false;$('badge').textContent='连接失败'}}
loadExistingRunOptions();refresh();setInterval(()=>{if(!polling)refresh()},5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PipelineUI/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 170 * 1024 * 1024:
            raise ValueError("上传内容超过 32 MB")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            default_image = str(DEFAULT_IMAGE) if DEFAULT_IMAGE.is_file() else ""
            body = HTML.replace("__DEFAULT_IMAGE__", html.escape(default_image, quote=True)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/compare":
            path = STATIC_ROOT / "comparison.html"
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/status":
            self.send_json({**CONTROLLER.status(), "ai_editor": AI_EDITOR.status(CONTROLLER.run_dir)})
        elif parsed.path == "/api/examples":
            self.send_json({"runs": existing_runs.list_runs()})
        elif parsed.path == "/api/assistant/job":
            self.send_json(ASSISTANT_JOBS.status() or {"state": "idle"})
        elif parsed.path == "/api/comparison":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                run_dir = Path(query["run_dir"][0]).resolve()
                studs = int(query["studs"][0])
            except (KeyError, IndexError, ValueError):
                self.send_json({"error": "缺少有效的运行目录或 Stud 尺寸"}, status=400)
                return
            if not within_root(run_dir) or not run_dir.is_dir() or studs < 1 or studs > 10000:
                self.send_json({"error": "无效的运行目录或 Stud 尺寸"}, status=400)
                return
            original = run_dir / f"lego-{studs}" / "model.mpd"
            comparison = bundle_comparison(run_dir, studs, original) if original.is_file() else None
            if comparison is None:
                self.send_json({"error": "此尺寸尚无可用的 Bundle 比较报告"}, status=404)
                return
            self.send_json({
                "studs": studs,
                "run_dir": str(run_dir),
                "ldraw_available": (LDRAW_ROOT / "parts").is_dir() and (LDRAW_ROOT / "LDConfig.ldr").is_file(),
                "comparison": comparison,
            })
        elif parsed.path.startswith("/static/"):
            path = (STATIC_ROOT / parsed.path.removeprefix("/static/")).resolve()
            if not path.is_file() or path.suffix != ".js" or not path.is_relative_to(STATIC_ROOT.resolve()):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)
        elif parsed.path.startswith("/ldraw/"):
            relative = urllib.parse.unquote(parsed.path.removeprefix("/ldraw/"))
            path = (LDRAW_ROOT / relative).resolve()
            if (
                not path.is_file()
                or not path.is_relative_to(LDRAW_ROOT.resolve())
                or path.suffix.lower() not in {".dat", ".ldr", ".png"}
            ):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = "image/png" if path.suffix.lower() == ".png" else "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)
        elif parsed.path == "/file":
            query = urllib.parse.parse_qs(parsed.query)
            path = Path(query.get("path", [""])[0]).resolve()
            if not path.is_file() or not within_root(path):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            self.end_headers()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    self.wfile.write(chunk)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        try:
            data = self.read_json()
            if self.path == "/api/start":
                if AI_EDITOR.is_running():
                    raise RuntimeError("3D 模型编辑正在运行")
                CONTROLLER.start(data)
                self.send_json({"ok": True})
            elif self.path == "/api/stop":
                CONTROLLER.stop()
                self.send_json({"ok": True})
            elif self.path == "/api/assistant/stop":
                ASSISTANT_JOBS.stop()
                self.send_json({"ok": True})
            elif self.path == "/api/import-run":
                run_dir = CONTROLLER.import_existing(str(data.get("id", "")))
                self.send_json({"ok": True, "run_dir": str(run_dir)})
            elif self.path == "/api/ai-edit/start":
                if CONTROLLER.is_running():
                    raise RuntimeError("主流水线正在运行，请先等待它到达验收点")
                AI_EDITOR.start(CONTROLLER.run_dir, {"instruction": data.get("instruction", "")})
                self.send_json({"ok": True})
            elif self.path == "/api/ai-edit/upload":
                if CONTROLLER.is_running() or AI_EDITOR.is_running():
                    raise RuntimeError("当前任务正在运行，请等待完成")
                raw = base64.b64decode(str(data.get("data", "")), validate=True)
                if not raw or len(raw) > 120 * 1024 * 1024:
                    raise ValueError("3D 模型为空或超过 120 MB")
                run_dir, model = CONTROLLER.import_model(str(data.get("name", "model.glb")), raw)
                self.send_json({"ok": True, "run_dir": str(run_dir), "model": str(model)})
            elif self.path == "/api/ai-edit/select-generated":
                model = CONTROLLER.select_generated_model()
                self.send_json({"ok": True, "model": str(model)})
            elif self.path == "/api/ai-edit/stop":
                AI_EDITOR.stop()
                self.send_json({"ok": True})
            elif self.path == "/api/ai-edit/accept":
                AI_EDITOR.accept(CONTROLLER.run_dir)
                self.send_json({"ok": True})
            elif self.path == "/api/ai-edit/reset":
                AI_EDITOR.reset(CONTROLLER.run_dir)
                self.send_json({"ok": True})
            elif self.path == "/api/ai-edit/resume":
                AI_EDITOR.resume(CONTROLLER.run_dir)
                self.send_json({"ok": True})
            elif self.path == "/api/upload":
                if CONTROLLER.is_running() or AI_EDITOR.is_running():
                    raise RuntimeError("当前任务正在运行，请稍后上传图片")
                raw = base64.b64decode(str(data.get("data", "")), validate=True)
                if not raw or len(raw) > 50 * 1024 * 1024:
                    raise ValueError("图片为空或超过 50 MB")
                name = str(data.get("name", "upload.png"))
                if Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("请上传 PNG、JPEG 或 WebP；HEIC 请先导出为 JPEG")
                try:
                    from io import BytesIO
                    from PIL import Image
                    with Image.open(BytesIO(raw)) as image:
                        image.verify()
                except Exception as exc:
                    raise ValueError("图片无法解码，请重新导出为 PNG 或 JPEG") from exc
                UPLOADS.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time_ns() % 1_000_000):06d}"
                path = UPLOADS / f"{stamp}-{safe_name(str(data.get('name', 'upload.png')))}"
                path.write_bytes(raw)
                output_dir = default_run_dir(path)
                CONTROLLER.set_uploaded_image(path, output_dir)
                self.send_json({"path": str(path), "output_dir": str(output_dir),
                                "url": "/file?path=" + urllib.parse.quote(str(path))})
            elif self.path == "/api/revision/upload":
                run_dir = Path(str(data.get("run_dir", ""))).resolve()
                if not run_dir.is_dir() or not within_root(run_dir):
                    raise ValueError("无效的运行目录")
                relative = str(data.get("relative", ""))
                raw = base64.b64decode(str(data.get("data", "")), validate=True)
                if len(raw) > 20 * 1024 * 1024:
                    raise ValueError("修改版文件不能超过 20 MB")
                result = ui_revisions.add_candidate(run_dir, relative, str(data.get("name", "")), raw, ROOT)
                self.send_json({"ok": True, **result})
            elif self.path == "/api/revision/choose":
                run_dir = Path(str(data.get("run_dir", ""))).resolve()
                relative = str(data.get("relative", ""))
                choice = str(data.get("choice", ""))
                if not run_dir.is_dir() or not within_root(run_dir):
                    raise ValueError("无效的运行目录")
                if relative in {"input/foreground.png", "unitex/mv_rgb.png"} and choice == "candidate":
                    with CONTROLLER.lock:
                        if CONTROLLER.process is not None and CONTROLLER.process.poll() is None:
                            raise RuntimeError("流水线正在运行，不能切换到修改版")
                texture_input = None
                texture_shape = None
                if relative == "unitex/mv_rgb.png" and choice == "candidate":
                    try:
                        texture_input = ui_revisions.available_artifact(
                            run_dir, "input/foreground.png", ROOT
                        )
                        texture_shape = ui_revisions.available_artifact(
                            run_dir, "hunyuan/mesh_shape_review_clean.glb", ROOT
                        )
                    except ValueError as exc:
                        raise ValueError("要继续六视图修改版，原运行目录须有透明底输入和无色模型")
                record = ui_revisions.choose(run_dir, relative, choice, ROOT)
                response: dict[str, object] = {"ok": True, "selected_path": record[choice]}
                if relative == "input/foreground.png" and choice == "candidate":
                    image = Path(str(record["candidate"]))
                    branch = ui_revisions.create_input_branch(run_dir, image, ROOT)
                    ui_revisions.register_generated_pair(
                        branch, relative, Path(str(record["original"])), image, ROOT
                    )
                    ui_revisions.choose(branch, relative, "candidate", ROOT)
                    CONTROLLER.activate_branch(branch, image)
                elif relative == "unitex/mv_rgb.png" and choice == "candidate":
                    assert texture_input is not None and texture_shape is not None
                    image = texture_input
                    shape = texture_shape
                    sheet = Path(str(record["candidate"]))
                    branch = ui_revisions.create_texture_branch(run_dir, sheet, image, shape, ROOT)
                    ui_revisions.register_generated_pair(
                        branch, relative, Path(str(record["original"])), sheet, ROOT
                    )
                    ui_revisions.choose(branch, relative, "candidate", ROOT)
                    CONTROLLER.activate_branch(branch, image)
                self.send_json(response)
            elif self.path == "/api/assistant/chat":
                run_text = str(data.get("run_dir", ""))
                run_dir = Path(run_text).resolve() if run_text else None
                if run_dir is not None and (not run_dir.is_dir() or not within_root(run_dir)):
                    raise ValueError("无效的运行目录")
                history = data.get("history", [])
                if not isinstance(history, list):
                    raise ValueError("无效的对话记录")
                result = ui_assistant.chat(
                    run_dir=run_dir, mode=str(data.get("mode", "")),
                    value=str(data.get("value", "")), message=str(data.get("message", "")),
                    history=history,
                    dispatch=lambda tool, args: self.dispatch_assistant(run_dir, tool, args),
                )
                self.send_json(result)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": f"服务器错误：{exc}"}, status=500)

    @staticmethod
    def dispatch_assistant(run_dir: Path | None, tool: str, args: dict[str, object]) -> dict[str, object]:
        if run_dir is None:
            raise ValueError("请先选择一个已有运行目录")
        with CONTROLLER.lock:
            if CONTROLLER.process is not None and CONTROLLER.process.poll() is None:
                raise RuntimeError("流水线正在运行，请在当前阶段结束后修改")
        return ASSISTANT_JOBS.submit(run_dir, tool, args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7865)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--run-dir", type=Path, help="载入已有运行目录，仅用于显示产物")
    args = parser.parse_args()
    if args.run_dir is not None:
        run_dir = args.run_dir.resolve()
        if not run_dir.is_dir() or not within_root(run_dir):
            parser.error("--run-dir 必须是项目内已有的运行目录")
        CONTROLLER.run_dir = run_dir
        CONTROLLER.message = "已载入现有结果"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Pipeline UI: {url}", flush=True)
    print("Press Ctrl+C to stop the local server.", flush=True)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
