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


from project_config import load_project_config

ROOT = Path(__file__).resolve().parent
load_project_config(ROOT)
PIPELINE = ROOT / "pipeline_2d_to_lego.py"
LEGO_ROOT = ROOT / "legolization"
LEGO_PYTHON = LEGO_ROOT / ".venv" / "Scripts" / "python.exe"
SCALE_RECOMMENDER = ROOT / "lego_scale_recommendation.py"
RECOMMENDATION_MIN_STUDS = 8
RECOMMENDATION_MAX_STUDS = 128
DEFAULT_IMAGE = ROOT / "models" / "chicken" / "7ken.png"
UPLOADS = ROOT / "ui_uploads"


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(name).name).strip("-.")
    return stem or "upload.png"


def default_run_dir(image: Path) -> Path:
    return ROOT / "runs" / f"{image.stem}-hunyuan-unitex"


def within_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


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
            result.append(
                {
                    "label": label,
                    "relative": relative,
                    "kind": kind,
                    "size": path.stat().st_size,
                    "url": "/file?path=" + urllib.parse.quote(str(path)),
                }
            )
    lego_models: list[tuple[int, Path]] = []
    for path in run_dir.glob("lego-*/model.mpd"):
        match = re.fullmatch(r"lego-(\d+)", path.parent.name)
        if match:
            lego_models.append((int(match.group(1)), path))
    for studs, path in sorted(lego_models):
        result.append(
            {
                "label": f"LEGO 模型（{studs} studs）",
                "relative": path.relative_to(run_dir).as_posix(),
                "kind": "model",
                "size": path.stat().st_size,
                "url": "/file?path=" + urllib.parse.quote(str(path)),
            }
        )
    legacy = run_dir / "lego" / "model.mpd"
    lego_input = run_dir / "oriented" / "lego_input.ply"
    if legacy.is_file() and (
        not lego_input.is_file() or legacy.stat().st_mtime >= lego_input.stat().st_mtime
    ):
        result.append(
            {
                "label": "LEGO 模型（旧版输出目录）",
                "relative": "lego/model.mpd",
                "kind": "model",
                "size": legacy.stat().st_size,
                "url": "/file?path=" + urllib.parse.quote(str(legacy)),
            }
        )
    return result


def build_command(payload: dict[str, object]) -> tuple[list[str], Path, Path]:
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

    command = [sys.executable, str(PIPELINE), str(image), "--output-dir", str(run_dir)]
    if payload.get("accept_preprocess"):
        command.append("--accept-preprocess")
    if payload.get("accept_geometry"):
        command.append("--accept-geometry")
    if payload.get("accept_unitex"):
        command.append("--accept-unitex")
    if payload.get("build_lego"):
        command.append("--build-lego")
    if payload.get("bundle"):
        command.append("--bundle")
    if payload.get("force"):
        command.append("--force")
    projection = str(payload.get("projection", "ltm"))
    if projection not in ("ltm", "direct"):
        raise ValueError("无效的 UniTEX 投影方式")
    command.extend(["--unitex-projection", projection])
    try:
        unitex_gpu = int(payload.get("unitex_gpu", 1))
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

    def start(self, payload: dict[str, object]) -> None:
        command, image, run_dir = build_command(payload)
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("流水线正在运行，请等待或先停止当前任务")
            self.logs.clear()
            self.command = command
            self.image = image
            self.run_dir = run_dir
            self.returncode = None
            self.message = "正在启动"
            self.started_at = time.time()
            self.stop_requested = False
            self.last_output_at = self.started_at
            self.lego_started_at = None
            worker = threading.Thread(target=self._run, daemon=True)
            worker.start()

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
        data["artifacts"] = artifact_inventory(run_dir)
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
        mesh = run_dir / "oriented" / "lego_input.ply"
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
                timeout=300,
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


CONTROLLER = PipelineController()


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
.hint{color:var(--muted);font-size:12px;line-height:1.55}.recommendation{margin-top:8px;padding:10px 11px;border:1px solid #cbd8c7;border-radius:10px;background:#edf5e9;color:#31593d;font-size:13px;line-height:1.45}.recommendation button{padding:5px 9px;margin-left:7px;background:#d5e8cf}.steps{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin-bottom:16px}.step{font-size:12px;padding:9px 7px;border-radius:9px;text-align:center;background:#ece8dd;color:#73766c}.step.active{background:#dfeade;color:#285a3f;font-weight:750}.step.wait{background:#f4e5b2;color:#755b00;font-weight:750}.notices{display:grid;gap:8px;margin:10px 0}.notice{border-radius:10px;padding:10px 12px;font-size:13px;line-height:1.5}.notice.warning{background:#fff1c7;color:#715300;border:1px solid #e2c56c}.notice.error{background:#f7d7d2;color:#842f23;border:1px solid #dc9d94}
.log{height:310px;overflow:auto;white-space:pre-wrap;background:#20231f;color:#dce8d5;border-radius:13px;padding:14px;font:12px/1.5 Consolas,monospace}.meta{display:flex;flex-wrap:wrap;gap:8px;color:var(--muted);font-size:12px;margin:10px 0}.meta span{background:#f0ede5;padding:5px 8px;border-radius:7px}.artifacts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}.artifact{border:1px solid var(--line);border-radius:13px;overflow:hidden;background:white}.artifact img{display:block;width:100%;height:190px;object-fit:contain;background:repeating-conic-gradient(#eee 0 25%,#ddd 0 50%) 50%/20px 20px}.artifact .info{padding:10px}.artifact b{display:block;font-size:13px}.artifact a{font-size:12px;color:var(--blue);text-decoration:none}.empty{color:var(--muted);padding:35px 10px;text-align:center;border:1px dashed var(--line);border-radius:12px}
@media(max-width:900px){.grid{grid-template-columns:1fr}.artifacts{grid-template-columns:1fr 1fr}.hero{align-items:start;flex-direction:column}}@media(max-width:560px){.shell{padding:14px}.artifacts,.checks{grid-template-columns:1fr}.steps{grid-template-columns:1fr 1fr}.hero h1{font-size:25px}}
</style></head>
<body><div class="shell">
<div class="hero"><div><h1>2D → Hunyuan → UniTEX → LEGO</h1><p>本地实验控制台 · 阶段验收 · 断点续跑</p></div><div id="badge" class="badge">就绪</div></div>
<div class="grid"><section class="panel"><h2>输入与运行设置</h2>
<label>图片路径</label><div class="fileline"><input id="imagePath" type="text" value="__DEFAULT_IMAGE__"><button onclick="document.getElementById('file').click()">上传图片</button></div><input id="file" type="file" accept="image/*" hidden onchange="uploadFile(this)">
<label>输出目录（留空自动生成）</label><input id="outputDir" type="text" placeholder="留空使用项目目录下的 runs 文件夹">
<div class="checks">
<label class="check"><input id="acceptPre" type="checkbox"><span>已检查抠图<br><small>允许进入 Hunyuan</small></span></label>
<label class="check"><input id="acceptGeo" type="checkbox"><span>已检查几何<br><small>允许进入 UniTEX</small></span></label>
<label class="check"><input id="acceptTex" type="checkbox"><span>已检查六视图<br><small>允许贴图和校正</small></span></label>
<label class="check"><input id="buildLego" type="checkbox"><span>允许构建 LEGO<br><small>最终批准</small></span></label>
</div>
<label>UniTEX 投影</label><select id="projection"><option value="ltm">LTM（推荐，补全不可见面）</option><option value="direct">Direct（快速诊断）</option></select>
<label>远端 UniTEX GPU</label><select id="unitexGpu"><option value="1">GPU 1（推荐，当前空闲）</option><option value="0">GPU 0</option></select>
<label>LEGO 宽度（Stud）</label><input id="studs" type="number" min="8" max="128" value="24">
<div id="legoRecommendation" class="recommendation" hidden></div>
<div class="checks"><label class="check"><input id="bundle" type="checkbox"><span>生成完整 Bundle</span></label><label class="check"><input id="force" type="checkbox"><span>强制重跑已有阶段</span></label></div>
<div class="actions"><button class="primary" id="runButton" onclick="startRun()">运行到下一验收点</button><button class="danger" onclick="stopRun()">停止当前任务</button></div>
<p class="hint">透明底标准图会自动跳过抠图。没有勾选的验收项会成为本次运行的停止点。旧结果会自动复用；一般不要勾选“强制重跑”。</p>
</section>
<section class="panel"><div class="steps" id="steps"><div class="step">输入</div><div class="step">Hunyuan 几何</div><div class="step">UniTEX 六视图</div><div class="step">贴图与校正</div><div class="step">LEGO</div></div>
<div class="meta"><span id="runDir">尚未运行</span><span id="elapsed">0 秒</span></div><div id="notices" class="notices"></div><div id="log" class="log">等待任务……</div>
<h2 style="margin-top:18px">产物与预览</h2><div id="artifacts" class="empty">运行后会在这里显示检查图和模型下载链接</div>
</section></div></div>
<script>
const $=id=>document.getElementById(id); let polling=false;
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)throw new Error(d.error||'请求失败');return d}
async function uploadFile(input){if(!input.files.length)return;const f=input.files[0];const reader=new FileReader();reader.onload=async()=>{try{const d=await api('/api/upload',{name:f.name,data:reader.result.split(',')[1]});$('imagePath').value=d.path;$('outputDir').value=d.output_dir}catch(e){alert(e.message)}};reader.readAsDataURL(f)}
async function startRun(){try{await api('/api/start',{image_path:$('imagePath').value,output_dir:$('outputDir').value,accept_preprocess:$('acceptPre').checked,accept_geometry:$('acceptGeo').checked,accept_unitex:$('acceptTex').checked,build_lego:$('buildLego').checked,bundle:$('bundle').checked,force:$('force').checked,projection:$('projection').value,unitex_gpu:$('unitexGpu').value,target_studs:$('studs').value});polling=true;refresh()}catch(e){alert(e.message)}}
async function stopRun(){try{await api('/api/stop',{})}catch(e){alert(e.message)}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function stageIndex(state,arts){const s=state.stage||'';if(s==='foreground_review')return 0;if(s==='geometry_review')return 1;if(s==='unitex_review')return 2;if(s==='oriented_review')return 3;if(s==='complete')return 4;if(arts.some(a=>a.relative.includes('hunyuan/')))return 1;return 0}
function showRecommendation(r){const box=$('legoRecommendation');if(!r||r.status==='unavailable'){box.hidden=true;return}box.hidden=false;if(r.status==='ready'){box.innerHTML=`Legolization 推荐：<b>${r.target_studs} Stud</b><button type="button" onclick="$('studs').value=${r.target_studs}">使用推荐值</button><br><small>在 ${r.minimum}–${r.maximum} Stud 范围内按模型体素化质量自动选择；不保证最终一定可构建。</small>`}else if(r.status==='calculating'){box.textContent=`正在计算 Legolization 推荐宽度（${r.minimum}–${r.maximum} Stud）……`}else if(r.status==='waiting'){box.textContent='当前任务完成后计算 Legolization 推荐宽度……'}else{box.textContent='推荐宽度计算失败：'+(r.error||'未知错误')}}
async function refresh(){try{const r=await fetch('/api/status');const d=await r.json();$('badge').textContent=d.message+(d.running?' · '+d.elapsed+'s':'');$('runButton').disabled=d.running;showRecommendation(d.lego_recommendation);const notices=d.notices||[];$('notices').innerHTML=notices.map(n=>`<div class="notice ${n.level==='error'?'error':'warning'}">${esc(n.text)}</div>`).join('');$('log').textContent=d.logs||'等待任务……';$('log').scrollTop=$('log').scrollHeight;$('runDir').textContent=d.run_dir||'尚未运行';$('elapsed').textContent=d.elapsed+' 秒';const idx=stageIndex(d.pipeline_state||{},d.artifacts||[]);[...$('steps').children].forEach((e,i)=>{e.className='step '+(i<idx?'active':i===idx?'wait':'')});const arts=d.artifacts||[];$('artifacts').className=arts.length?'artifacts':'empty';$('artifacts').innerHTML=arts.length?arts.map(a=>`<div class="artifact">${a.kind==='image'?`<img src="${a.url}&v=${Date.now()}" alt="${esc(a.label)}">`:''}<div class="info"><b>${esc(a.label)}</b><a href="${a.url}" target="_blank">打开 / 下载 · ${(a.size/1048576).toFixed(2)} MB</a></div></div>`).join(''):'运行后会在这里显示检查图和模型下载链接';polling=!!d.running;if(d.running)setTimeout(refresh,1200)}catch(e){polling=false;$('badge').textContent='连接失败'}}
refresh();setInterval(()=>{if(!polling)refresh()},5000);
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
        if length > 32 * 1024 * 1024:
            raise ValueError("上传内容超过 32 MB")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = HTML.replace("__DEFAULT_IMAGE__", html.escape(str(DEFAULT_IMAGE), quote=True)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/status":
            self.send_json(CONTROLLER.status())
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
                CONTROLLER.start(data)
                self.send_json({"ok": True})
            elif self.path == "/api/stop":
                CONTROLLER.stop()
                self.send_json({"ok": True})
            elif self.path == "/api/upload":
                raw = base64.b64decode(str(data.get("data", "")), validate=True)
                if not raw:
                    raise ValueError("上传文件为空")
                UPLOADS.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time_ns() % 1_000_000):06d}"
                path = UPLOADS / f"{stamp}-{safe_name(str(data.get('name', 'upload.png')))}"
                path.write_bytes(raw)
                self.send_json({"path": str(path), "output_dir": str(default_run_dir(path))})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": f"服务器错误：{exc}"}, status=500)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7865)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
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
