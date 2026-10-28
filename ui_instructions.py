"""Read native booklet steps and run the isolated workbench for the web UI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_url(path: Path) -> str:
    return "/file?" + urllib.parse.urlencode({"path": str(path)})


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _inside_file(value: str | Path, root: Path) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("说明书文件不在项目内或尚未生成")
    return path


COLOURS = {
    "White": "白色", "Black": "黑色", "Red": "红色", "Orange": "橙色",
    "Yellow": "黄色", "Green": "绿色", "Blue": "蓝色", "Brown": "棕色",
    "Tan": "浅褐色", "Dark_Tan": "深褐色", "Pink": "粉色", "Coral": "珊瑚色",
    "Nougat": "牛轧糖色", "Medium_Nougat": "中牛轧糖色", "Dark_Brown": "深棕色",
    "Reddish_Brown": "红棕色", "Dark_Orange": "深橙色", "Lime": "黄绿色",
    "Light_Bluish_Grey": "浅蓝灰色", "Dark_Bluish_Grey": "深蓝灰色",
    "Light_Gray": "浅灰色", "Dark_Gray": "深灰色",
}


def part_name(key: str) -> str:
    for prefix, label in (
        ("slope_inverted_45_", "45° 反斜面砖"), ("slope_33_", "33° 斜面砖"),
        ("slope_45_", "45° 斜面砖"), ("brick_", "砖"), ("plate_", "薄板"), ("tile_", "光面板"),
    ):
        if key.startswith(prefix):
            return label + " " + key[len(prefix):].replace("x", " × ")
    return key


def read_booklet(result_path: Path, source_hash: str, root: Path) -> dict | None:
    """Require a complete booklet for these exact source bytes, never a nearby MPD."""
    try:
        row = _read(result_path)
        if row.get("input_sha256") != source_hash or not row.get("booklet_complete"):
            return None
        run = result_path.parent
        if _read(run / "run_identity.json").get("source_sha256") != source_hash:
            return None
        pdf = _inside_file(row["files"]["pdf"], root)
        book = pdf.parent
        plan, bom = _read(run / "plan.json"), _read(run / "bom.json")
        if not plan["steps"] or len(plan["steps"]) != len(bom["steps"]):
            return None
        summary = _read(run / "stability_summary.json") if (run / "stability_summary.json").is_file() else {}
        verified = {item["index"]: item["stable"] for item in summary.get("steps", [])}
        risks = set(summary.get("static_risk_steps", []))
        steps = []
        for index, (step, taken) in enumerate(zip(plan["steps"], bom["steps"]), 1):
            if step["index"] != index or taken["step"] != index:
                return None
            if sum(p["quantity"] for p in taken["parts"]) != taken["brick_count"]:
                return None
            image = _inside_file(book / "steps" / f"step_{index:04d}.png", root)
            parts = []
            for part in taken["parts"]:
                number = str(part["ldraw_part"])
                if not re.fullmatch(r"[A-Za-z0-9_-]+", number):
                    return None
                thumbnail = _inside_file(book / "parts" / f'{number}_{int(part["colour_code"])}.png', root)
                parts.append({
                    "name": part_name(str(part["part_key"])), "number": number,
                    "colour": COLOURS.get(part["colour_name"], part["colour_name"].replace("_", " ")),
                    "colour_code": part["colour_code"], "quantity": part["quantity"],
                    "image_url": file_url(thumbnail),
                })
            opposite = book / "details" / f"step_{index:04d}_opposite.png"
            alternate = file_url(_inside_file(opposite, root)) if opposite.is_file() else None
            warning = "本步静力检查未通过，拼搭时需要支撑。" if index in risks or verified.get(index) is False else ""
            if step.get("insertion_fragile"):
                warning += " 本步存在插入受力风险，请支撑连接处。"
            role = "装入已完成的子组件" if step.get("attaches") else "单独构建子组件" if step.get("submodel") else "主模型"
            steps.append({
                "index": index, "image_url": file_url(image), "alternate_url": alternate,
                "parts": parts, "quantity": taken["brick_count"], "role": role,
                "attaches": step.get("attaches"), "submodel": step.get("submodel"),
                "rotate": bool(step.get("rotstep")), "warning": warning,
            })
        if sum(s["quantity"] for s in steps) != bom["brick_count"]:
            return None
        return {"source_sha256": source_hash, "steps": steps, "pdf_url": file_url(pdf),
                "stability": summary.get("status", row.get("stability_status", "PENDING"))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


class InstructionService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.workbench = self.root / "instructions_workbench"
        self.lock = threading.Lock()
        self.serial = threading.Lock()
        self.jobs: dict[str, dict] = {}

    def output_root(self) -> Path:
        config = self.workbench / "config/local.json"
        value = _read(config).get("output_root", "outputs") if config.is_file() else self.root / "runs/lego-instructions"
        output = Path(value)
        output = (output if output.is_absolute() else self.workbench / output).resolve()
        if not output.is_relative_to(self.root):
            raise ValueError("说明书输出目录需要配置在本项目内")
        return output

    def cached(self, source_hash: str) -> dict | None:
        paths = self.output_root().glob(f"instructions_first_{source_hash[:12]}_*/workflow_result.json")
        for path in sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True):
            result = read_booklet(path, source_hash, self.root)
            if result is not None:
                return result
        return None

    def status(self, model: Path) -> dict:
        source_hash = digest(model)
        booklet = self.cached(source_hash)
        with self.lock:
            job = dict(self.jobs.get(source_hash, {}))
        if booklet:
            return {"state": "ready", "instructions": booklet,
                    "analysis_running": booklet["stability"] == "PENDING"}
        if job and job["state"] != "complete":
            summary = job.get("summary", "等待生成")
            if job.get("log") and job["state"] == "generating":
                summary = self._progress(Path(job["log"]))
            return {"state": job["state"], "summary": summary}
        return {"state": "missing", "summary": "此模型还没有拼搭说明书"}

    def start(self, model: Path, title: str = "LEGO 模型") -> dict:
        state = self.status(model)
        if state["state"] in {"ready", "queued", "generating"}:
            return state
        python = self.workbench / ".venv/Scripts/python.exe"
        if not python.is_file() or not (self.workbench / "run.py").is_file() or not (self.workbench / "config/local.json").is_file():
            raise ValueError("请先按说明配置本机 instructions_workbench 说明书环境")
        source_hash = digest(model)
        with self.lock:
            if self.jobs.get(source_hash, {}).get("state") in {"queued", "generating"}:
                return {"state": self.jobs[source_hash]["state"], "summary": "说明书任务已启动"}
            self.jobs[source_hash] = {"state": "queued", "summary": "正在等待说明书生成"}
        threading.Thread(target=self._worker, args=(model, source_hash, title[:150]), daemon=True).start()
        return self.status(model)

    def _update(self, source_hash: str, **changes) -> None:
        with self.lock:
            self.jobs[source_hash].update(changes)

    @staticmethod
    def _progress(log: Path) -> str:
        try:
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 12000))
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                if "零件小图" in line:
                    return line.replace("零件小图", "正在生成零件预览")
                if "补充视角" in line:
                    return "正在生成补充视角"
                if "[6/6]" in line:
                    return "正在整理拼搭步骤和 PDF"
                if "[5/6]" in line or "渲染" in line or "rendering" in line:
                    return "正在渲染拼搭步骤图"
                if "规划完成" in line:
                    return "拼搭步骤已规划，准备渲染"
        except OSError:
            pass
        return "正在检查模型并规划拼搭步骤"

    def _worker(self, model: Path, source_hash: str, title: str) -> None:
        try:
            with self.serial:
                if digest(model) != source_hash:
                    raise ValueError("模型已更新，请重新打开说明书")
                # A request queued for another model may now have a native cache.
                if self.cached(source_hash):
                    self._update(source_hash, state="complete")
                    return
                directory = self.output_root() / "ui-jobs"
                directory.mkdir(parents=True, exist_ok=True)
                log = directory / f"{source_hash[:16]}-{time.time_ns()}.log"
                self._update(source_hash, state="generating", log=str(log))
                env = os.environ.copy()
                env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
                python = self.workbench / ".venv/Scripts/python.exe"
                command = [str(python), "-u", str(Path(__file__).resolve()), "--native-generate",
                           str(self.workbench), str(model), title]
                with log.open("wb") as output:
                    process = subprocess.Popen(command, cwd=self.workbench, env=env, stdout=output,
                                               stderr=subprocess.STDOUT,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    code = process.wait()
                if digest(model) != source_hash:
                    raise ValueError("生成期间模型已更新，请重新打开说明书")
                if self.cached(source_hash):
                    self._update(source_hash, state="complete")
                    return
                detail = log.read_text(encoding="utf-8", errors="replace")[-1600:]
                raise RuntimeError(f"说明书生成失败（退出码 {code}）：{detail}")
        except Exception as exc:  # noqa: BLE001
            self._update(source_hash, state="error", summary=str(exc))


def native_generate(workbench: Path, source: Path, title: str) -> int:
    """Use the native planning/rendering APIs without the team's old model whitelist.

    UI cache lookup is owned by InstructionService. The workbench's historical
    team index contains paths to published samples that may not be installed.
    This adapter always uses this source and the configured local output root.
    """
    from argparse import Namespace

    sys.path.insert(0, str(workbench / "src"))
    from lego_workbench.config import read_settings
    from lego_workbench.storage import model_lock, read_json, write_json
    from lego_workbench.planning import prepare
    from lego_workbench.rendering.pipeline import render_and_booklet
    from lego_workbench.bounded_analysis import run as analyze
    from lego_workbench.workflow import archive_result, check_booklet, refresh_text, state_report

    settings = read_settings(workbench / "config/local.json")
    source_hash = digest(source)
    with model_lock(settings.output_root, source_hash):
        run = prepare(settings, source)
        args = Namespace(renderer=settings.renderer, ldraw_dir=settings.ldraw_dir, title=title,
                         expected_sha256=source_hash, analysis_state={"status": "PENDING"})
        book = render_and_booklet(args, "custom", run, source, draft=True)
        plan, bom = read_json(run / "plan.json"), read_json(run / "bom.json")
        checks = check_booklet(book, plan, bom)
        state_report(run, {"status": "PENDING"})
        result = {
            "model": title, "input_sha256": source_hash, "booklet_complete": True,
            "generation_status": "PASSED", "instruction_certified": False, "stability_status": "PENDING",
            "checks": checks, "team_delivery": str(run / "Result.zip"),
            "files": {"pdf": str(book / "instructions_cn.pdf"), "html": str(book / "instructions_cn.html"),
                      "bom": str(run / "bom.csv"), "model_steps": str(run / "model_steps.mpd"),
                      "stability_summary": str(run / "stability_summary.json"),
                      "stability_report": str(run / "stability_report.html")},
        }
        write_json(run / "workflow_result.json", result)
        archive_result(run, source, book, result)
        print("说明书已生成，稳定性待分析", flush=True)
        state = state_report(run, analyze(settings, source, run, limit_seconds=1800))
        result["files"]["pdf"] = str(refresh_text(run, book, title, state))
        result.update(stability_status=state["status"], analysis=state,
                      checks=check_booklet(book, plan, bom))
        write_json(run / "workflow_result.json", result)
        archive_result(run, source, book, result)
        if digest(source) != source_hash:
            raise ValueError("生成期间原模型已更新")
        return 0 if state["status"] == "PASSED" else 2


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--native-generate":
        raise SystemExit("usage: ui_instructions.py --native-generate WORKBENCH MODEL TITLE")
    raise SystemExit(native_generate(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]))
