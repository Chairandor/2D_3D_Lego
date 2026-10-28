"""Bounded, non-destructive jobs offered to the pipeline's Qwen assistant."""

from __future__ import annotations

import base64
import io
import json
import math
import os
from project_config import terminate_process_tree
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops

import ui_revisions
import proxe_integration as proxe
import ui_six_view


CHAT_TOOLS = [
    {
        "type": "function", "function": {
            "name": "edit_stage_image",
            "description": "实际生成透明底输入或 UniTEX 六视图的修改版 PNG。六视图仅允许颜色修改：最初版本的蒙版、轮廓、边缘、背景、尺寸、六格位置永久固定，绝不允许修改几何或轮廓。仅用于用户明确要求修改图片时。",
            "parameters": {
                "type": "object", "properties": {
                    "target": {"type": "string", "enum": ["input/foreground.png", "unitex/mv_rgb.png"]},
                    "instruction": {"type": "string", "description": "具体编辑要求，保留主体位置和未提及内容"},
                }, "required": ["target", "instruction"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function", "function": {
            "name": "run_lego_skill",
            "description": "按 Legolization skill 的工作流执行受限的 LEGO 操作，保留原文件；耗时任务后台运行。",
            "parameters": {
                "type": "object", "properties": {
                    "skill": {"type": "string", "enum": [
                        "prepare-lego-input", "legolize-model", "optimize-lego-build",
                        "analyze-lego-assembly", "repair-lego-assembly", "render-ldraw",
                        "publish-lego-instructions", "extend-lego-part-support",
                    ]},
                    "quality": {"type": "string", "enum": ["fast", "balanced"]},
                    "studs": {"type": "integer", "description": "仅在用户明确指定 Stud 宽度时填写"},
                    "special_parts": {"type": "boolean", "description": "尝试启用斜坡、光面砖和 SNOT 饰面；不保证一定增加"},
                    "part_id": {"type": "string", "description": "扩展零件支持时用户指定的 LDraw 零件号"},
                }, "required": ["skill"], "additionalProperties": False,
            },
        },
    },
]


def _safe_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("服务器尚未配置 DASHSCOPE_API_KEY；请填写 config.local.json 中的密钥并重启 UI")
    return key


def _base_url() -> str:
    value = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    if not value.startswith("https://") or not value.endswith("/compatible-mode/v1"):
        raise RuntimeError("DASHSCOPE_BASE_URL 必须是百炼 HTTPS 兼容接口地址")
    host = urllib.parse.urlparse(value).hostname or ""
    if host != "dashscope.aliyuncs.com" and not host.endswith(".maas.aliyuncs.com"):
        raise RuntimeError("DASHSCOPE_BASE_URL 不是百炼域名")
    return value


def _image_endpoint() -> str:
    base = _base_url()
    return base.removesuffix("/compatible-mode/v1") + "/api/v1/services/aigc/multimodal-generation/generation"


def _image_output_size(size: tuple[int, int]) -> tuple[int, int]:
    """Choose an API-supported output size without rejecting a small input image."""
    pixels = size[0] * size[1]
    minimum, maximum = 512 * 512, 2048 * 2048
    if minimum <= pixels <= maximum:
        rounded = tuple(max(16, round(side / 16) * 16) for side in size)
        if minimum <= rounded[0] * rounded[1] <= maximum:
            return rounded
        if rounded[0] * rounded[1] < minimum:
            scale = math.sqrt(minimum / pixels)
            return tuple(math.ceil(side * scale / 16) * 16 for side in size)
    if pixels < minimum:
        scale = math.sqrt(minimum / pixels)
        return tuple(math.ceil(side * scale / 16) * 16 for side in size)
    scale = math.sqrt(maximum / pixels)
    return tuple(max(16, math.floor(side * scale / 16) * 16) for side in size)


def _image_edit(
    source: Path, instruction: str, target: str,
    *, reference: ui_six_view.SixViewReference | None = None,
) -> bytes:
    if not 1 <= len(instruction.strip()) <= 1000:
        raise ValueError("图片修改要求必须为 1–1000 字")
    if source.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("百炼图像编辑要求输入图片不超过 10 MB")
    with Image.open(source) as original:
        original.load()
        size = original.size
        output_size = _image_output_size(size)
        if target == "input/foreground.png":
            prompt = (
                "编辑这张透明底主体素材：" + instruction +
                "。保持透明背景、主体朝向和位置；不要新增背景、投影、文字或水印。"
            )
        else:
            if reference is None:
                raise ValueError("六视图修改必须提供最初版本的固定蒙版")
            if output_size != size:
                raise ValueError("当前六视图尺寸无法由图像 API 原尺寸返回，已拒绝自动缩放")
            original, _ = reference.lock(original)
            prompt = (
                "这是同一 3D 模型固定六相机视角的 3x2 贴图网格。" + instruction +
                "。" + ui_six_view.RULES
            )
        if target == "unitex/mv_rgb.png":
            locked_source = io.BytesIO()
            original.save(locked_source, format="PNG")
            encoded = base64.b64encode(locked_source.getvalue()).decode("ascii")
        else:
            encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        payload = {
            "model": os.environ.get("DASHSCOPE_IMAGE_MODEL", "qwen-image-2.1-pro"),
            "input": {"messages": [{"role": "user", "content": [
                {"image": "data:image/png;base64," + encoded}, {"text": prompt},
            ]}]},
            "parameters": {"size": f"{output_size[0]}*{output_size[1]}", "n": 1,
                           "watermark": False, "prompt_extend": False},
        }
        request = urllib.request.Request(
            _image_endpoint(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + _safe_key(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"千问图像编辑请求失败（HTTP {exc.code}）") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("无法连接千问图像编辑服务") from exc
        choices = result.get("output", {}).get("choices", [])
        content = choices[0].get("message", {}).get("content", []) if choices else []
        url = content[0].get("image") if content and isinstance(content[0], dict) else None
        if not isinstance(url, str):
            raise RuntimeError("图像编辑 API 未返回可下载的图片")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".aliyuncs.com"):
            raise RuntimeError("图像编辑 API 返回了不可信的图片地址")
        with urllib.request.urlopen(url, timeout=90) as response:
            raw = response.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise RuntimeError("生成图片超过 20 MB")
        with Image.open(io.BytesIO(raw)) as output:
            output.load()
            if output.size != output_size:
                raise RuntimeError(f"生成图片尺寸为 {output.size}，请求的是 {output_size}；已拒绝以免贴图错位")
            if output.size != size:
                output = output.resize(size, Image.Resampling.LANCZOS)
            if target == "input/foreground.png":
                result_image = output.convert("RGBA")
                original_alpha = original.convert("RGBA").getchannel("A")
                alpha = output.getchannel("A") if "A" in output.getbands() else original_alpha
                if alpha.getbbox() is None:
                    raise RuntimeError("生成图片的主体透明度为空")
                overlap = ImageChops.multiply(alpha, original_alpha)
                if overlap.getbbox() is None:
                    raise RuntimeError("生成图片与原主体位置完全不重合")
                result_image.putalpha(alpha)
            else:
                result_image, _ = reference.lock(output)
            stream = io.BytesIO()
            result_image.save(stream, format="PNG")
            return stream.getvalue()


class AgentJobManager:
    def __init__(self, root: Path, lego_cli: Path, lego_root: Path, ldraw_dir: Path) -> None:
        self.root = root.resolve()
        self.lego_cli = lego_cli
        self.lego_root = lego_root
        self.ldraw_dir = ldraw_dir
        self.lock = threading.Lock()
        self.current: dict[str, object] | None = None
        self.process: subprocess.Popen[str] | None = None
        self.cancelled: set[str] = set()

    def status(self) -> dict[str, object] | None:
        with self.lock:
            return dict(self.current) if self.current else None

    def submit(self, run_dir: Path, tool: str, args: dict[str, object]) -> dict[str, object]:
        run_dir = run_dir.resolve()
        if not run_dir.is_dir() or not run_dir.is_relative_to(self.root):
            raise ValueError("请先打开项目内一个有效的运行目录")
        if tool not in {"edit_stage_image", "run_lego_skill"}:
            raise ValueError("助手请求了未开放的操作")
        if not isinstance(args, dict):
            raise ValueError("操作参数格式无效")
        allowed = {"target", "instruction"} if tool == "edit_stage_image" else {
            "skill", "quality", "studs", "special_parts", "part_id"
        }
        if set(args) - allowed:
            raise ValueError("操作包含未开放的参数")
        if tool == "edit_stage_image":
            if args.get("target") not in {"input/foreground.png", "unitex/mv_rgb.png"}:
                raise ValueError("此图片阶段暂不支持自动编辑")
            if not isinstance(args.get("instruction"), str):
                raise ValueError("缺少图片修改要求")
            relative = str(args["target"])
            source = self._selected_path(run_dir, relative)
            if not source.is_file():
                raise ValueError("选定的图片尚未生成")
            if relative == "unitex/mv_rgb.png":
                ui_six_view.resolve_reference(run_dir, self.root)
        else:
            if args.get("skill") not in {
                "prepare-lego-input", "legolize-model", "optimize-lego-build",
                "analyze-lego-assembly", "repair-lego-assembly", "render-ldraw",
                "publish-lego-instructions", "extend-lego-part-support",
            }:
                raise ValueError("此 Legolization skill 暂未接入")
            if args.get("quality", "fast") not in {"fast", "balanced"}:
                raise ValueError("只开放 fast/balanced；更长的 exhaustive 需另行确认")
            if "studs" in args and (type(args["studs"]) is not int or not 8 <= args["studs"] <= 128):
                raise ValueError("Stud 宽度必须为 8–128 的整数")
            if "special_parts" in args and type(args["special_parts"]) is not bool:
                raise ValueError("异形件选项必须是布尔值")
            if args["skill"] == "extend-lego-part-support" and not re.fullmatch(r"[A-Za-z0-9_-]{2,30}", str(args.get("part_id", ""))):
                raise ValueError("扩展零件支持需要明确的零件编号")
        with self.lock:
            if self.current and self.current["state"] in {"queued", "running"}:
                raise RuntimeError("已有助手任务在运行，请等它完成")
            ident = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
            self.current = {
                "id": ident, "state": "queued", "tool": tool, "run_dir": str(run_dir),
                "summary": "等待启动", "result_path": "", "relative": "", "exit_code": None,
            }
        threading.Thread(target=self._worker, args=(ident, run_dir, tool, dict(args)), daemon=True).start()
        return self.status() or {}

    def _selected_path(self, run_dir: Path, relative: str) -> Path:
        pair = ui_revisions.selected_pair(run_dir, relative, self.root)
        if pair and pair.get("choice") in {"original", "candidate"}:
            return Path(str(pair[str(pair["choice"])]))
        path = run_dir / relative
        if path.is_file():
            return path
        try:
            return ui_revisions.available_artifact(run_dir, relative, self.root)
        except ValueError:
            return path

    def _set(self, ident: str, **updates: object) -> None:
        with self.lock:
            if self.current and self.current["id"] == ident and ident not in self.cancelled:
                self.current.update(updates)

    def _is_cancelled(self, ident: str) -> bool:
        with self.lock:
            return ident in self.cancelled

    def _worker(self, ident: str, run_dir: Path, tool: str, args: dict[str, object]) -> None:
        if self._is_cancelled(ident):
            return
        self._set(ident, state="running", summary="正在处理")
        try:
            if tool == "edit_stage_image":
                relative = str(args["target"])
                source = self._selected_path(run_dir, relative)
                if relative == "unitex/mv_rgb.png":
                    reference = ui_six_view.resolve_reference(run_dir, self.root)
                    content = _image_edit(source, str(args["instruction"]), relative, reference=reference)
                else:
                    content = _image_edit(source, str(args["instruction"]), relative)
                if self._is_cancelled(ident):
                    return
                result = ui_revisions.add_candidate(
                    run_dir, relative, "assistant.png", content, self.root, origin="assistant"
                )
                self._set(ident, state="complete", summary="图片修改版已生成，请在卡片上比较并选择",
                          result_path=result["candidate"], relative=relative)
            else:
                self._run_lego(ident, run_dir, args)
        except Exception as exc:  # noqa: BLE001
            self._set(ident, state="error", summary=str(exc))

    def _run_lego(self, ident: str, run_dir: Path, args: dict[str, object]) -> None:
        python = self.lego_cli.with_name("python.exe" if os.name == "nt" else "python")
        if not python.is_file():
            raise RuntimeError("本机未找到 Legolization 虚拟环境")
        skill = str(args["skill"])
        quality = str(args.get("quality", "fast"))
        job_dir = run_dir / "ui_assistant" / "jobs" / ident
        job_dir.mkdir(parents=True, exist_ok=False)
        mesh = proxe.selected_lego_input(run_dir) or self._selected_path(run_dir, "oriented/lego_input.ply")
        # The uv-generated Windows entry-point trampoline can fail after the
        # checkout is moved. Running the editable package via its venv Python
        # avoids that wrapper and does not modify the upstream repository.
        cmd: list[str] = [str(python), "-m", "legolization.main"]
        result_model: Path | None = None
        relative = ""
        if skill == "prepare-lego-input":
            if not mesh.is_file():
                raise ValueError("尚无 LEGO 输入网格")
            cmd += ["input", "inspect", str(mesh), "--json"]
        elif skill == "legolize-model":
            if not mesh.is_file():
                raise ValueError("尚无 LEGO 输入网格")
            studs = args.get("studs")
            if studs is None:
                raise ValueError("生成新 LEGO 模型必须明确 Stud 宽度")
            bundle = job_dir / "bundle"
            cmd += ["bundle", str(mesh), "-o", str(bundle), "--quality", quality,
                    "--render", "off", "--set", "input.mesh.up=y",
                    "--set", f"input.mesh.target_studs={studs}",
                    "--set", "input.mesh.colour_mode=sampled", "--json"]
            result_model = bundle / "model" / "model.mpd"
            relative = f"lego-{studs}/model.mpd"
        elif skill == "extend-lego-part-support":
            cmd += ["catalog", "infer", str(args["part_id"]), "-o", str(job_dir), "--json"]
        else:
            model = self._lego_model(run_dir, args.get("studs"))
            if model is None:
                raise ValueError("此操作需要先有一个 LEGO MPD 模型")
            model_path, relative = model
            if skill == "optimize-lego-build":
                bundle = job_dir / "optimized"
                cmd += ["bundle", str(model_path), "--retile", "-o", str(bundle),
                        "--quality", quality, "--render", "off", "--json"]
                result_model = bundle / "model" / "model.mpd"
            elif skill == "analyze-lego-assembly":
                cmd += ["analyze", str(model_path), "--no-repair", "--artifact-dir", str(job_dir / "analysis"), "--json"]
            elif skill == "repair-lego-assembly":
                result_model = job_dir / "repaired.mpd"
                cmd += ["analyze", str(model_path), "--repair", "--effort", "fast",
                        "--repair-output", str(job_dir / "repair"), "-o", str(result_model), "--json"]
            elif skill == "render-ldraw":
                cmd += ["model", "render", str(model_path), "-o", str(job_dir / "renders"), "--json"]
            elif skill == "publish-lego-instructions":
                cmd += ["bundle", str(model_path), "-o", str(job_dir / "instructions"),
                        "--render", "auto", "--json"]
        if args.get("special_parts") and skill in {"legolize-model", "optimize-lego-build"}:
            # This enables supported finishing passes; it does not guarantee a special part.
            insert = cmd.index("--json")
            cmd[insert:insert] = ["--set", "finishing.slopes=true", "--set", "finishing.tiles=true",
                                  "--set", "finishing.snot=true"]
        (job_dir / "request.json").write_text(json.dumps({
            "skill": skill, "studs": args.get("studs"), "source_relative": relative,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if self.ldraw_dir.is_dir():
            env["LDRAWDIR"] = str(self.ldraw_dir)
        self._set(ident, summary=f"Legolization 正在执行 {skill}")
        process = subprocess.Popen(cmd, cwd=self.lego_root, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                   start_new_session=os.name != "nt")
        with self.lock:
            self.process = process
        try:
            stdout, stderr = process.communicate()
        finally:
            with self.lock:
                self.process = None
        if self._is_cancelled(ident):
            return
        (job_dir / "result.json").write_text(stdout, encoding="utf-8")
        (job_dir / "progress.log").write_text(stderr[-200_000:], encoding="utf-8")
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Legolization 未返回可解析的 JSON（退出码 {process.returncode}）") from exc
        status = str(envelope.get("status", "error"))
        warnings = envelope.get("warnings", [])
        suffix = f"；警告 {len(warnings)} 条" if isinstance(warnings, list) and warnings else ""
        if process.returncode == 0 and result_model and not result_model.is_file():
            raise RuntimeError(f"{skill} 声称完成，但没有找到预期的 MPD；请查看任务报告")
        if process.returncode == 0 and result_model and result_model.is_file():
            original = self._selected_path(run_dir, relative)
            if original.is_file():
                candidate = ui_revisions.add_candidate(
                    run_dir, relative, "assistant.mpd", result_model.read_bytes(), self.root,
                    origin="assistant",
                )
                result_path = str(candidate["candidate"])
                summary = f"{skill} 完成，修改版已放入模型卡片供选择{suffix}"
            else:
                result_path = str(result_model)
                summary = f"{skill} 完成，新 LEGO 模型已生成{suffix}"
        else:
            result_path = str(job_dir / "result.json")
            if process.returncode == 0:
                summary = f"{skill} 完成；报告已保存{suffix}"
            elif process.returncode == 2:
                summary = f"{skill} 未找到可构建／已验证方案；已保存诊断，未生成可选修改版{suffix}"
            elif process.returncode == 3:
                summary = f"{skill} 仅得到部分或未确定结果；未自动采用{suffix}"
            else:
                summary = f"{skill} 失败（退出码 {process.returncode}）{suffix}"
        self._set(ident, state="complete" if process.returncode == 0 else "partial" if process.returncode in {2, 3} else "error",
                  summary=summary, exit_code=process.returncode, result_path=result_path, relative=relative)
        import ui_run_state

        ui_run_state.mark_changed(run_dir, self.root)

    def _lego_model(self, run_dir: Path, studs: object) -> tuple[Path, str] | None:
        parent = ui_revisions.branch_parent(run_dir, self.root)
        roots = (run_dir, parent) if parent else (run_dir,)
        relatives: set[str] = set()
        for directory in roots:
            assert directory is not None
            for path in directory.glob("lego-*/model.mpd"):
                match = re.fullmatch(r"lego-(\d+)", path.parent.name)
                if match and (studs is None or int(match.group(1)) == studs):
                    relatives.add(path.relative_to(directory).as_posix())
            for path in directory.glob("ui_assistant/jobs/*/bundle/model/model.mpd"):
                request = path.parents[2] / "request.json"
                try:
                    metadata = json.loads(request.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    metadata = {}
                source_relative = metadata.get("source_relative")
                if isinstance(source_relative, str) and (directory / source_relative).is_file():
                    continue
                if studs is None or metadata.get("studs") == studs:
                    relatives.add(path.relative_to(directory).as_posix())
        if len(relatives) > 1:
            raise ValueError("匹配到多个 LEGO 模型，请先明确目标；助手不会擅自挑选一版")
        for relative in relatives:
            path = self._selected_path(run_dir, relative)
            if path.is_file():
                return path, relative
        return None

    def stop(self) -> None:
        with self.lock:
            if self.current and self.current["state"] in {"queued", "running"}:
                ident = str(self.current["id"])
                self.cancelled.add(ident)
                self.current.update(state="cancelled", summary="已停止助手任务；原有产物不变")
            process = self.process
        if process is not None and process.poll() is None:
            terminate_process_tree(process)
