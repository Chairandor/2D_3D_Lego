from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from legolizer.processing3d.volume import UNCONSTRAINED_COLOR, VoxelVolume
from legolizer.constants import DEFAULT_PROJECT_DIR


def solver_environment() -> dict[str, str]:
    """Limit native library thread pools in solver children to avoid Windows allocation failures."""
    env = os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", OMP_NUM_THREADS="2", PYTHONIOENCODING="utf-8")
    return env


def export_legolization_npy(
    volume_or_path: VoxelVolume | str | Path,
    output_path: str | Path,
    fallback_color: int = 4,
    *,
    preserve_unconstrained: bool = False,
) -> Path:
    """导出 bundle 输入，或显式保留内部自由色号给原生布局接口。"""
    volume = (
        VoxelVolume.load(volume_or_path)
        if isinstance(volume_or_path, (str, Path))
        else volume_or_path
    )
    codes = np.full(volume.occupancy.shape, -1, dtype=np.int16)
    codes[volume.occupancy] = fallback_color
    if volume.colors is not None:
        # bundle validates LDraw codes on import and frees invisible cells itself.
        known_colors = volume.colors >= 0
        if preserve_unconstrained:
            known_colors |= volume.colors == UNCONSTRAINED_COLOR
        colored = volume.occupancy & known_colors
        codes[colored] = volume.colors[colored]
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    np.save(target, codes, allow_pickle=False)
    return target


def run_legolization_bundle(
    input_path: str | Path,
    output_dir: str | Path,
    project_dir: str | Path = DEFAULT_PROJECT_DIR,
    quality: str = "fast",
    duration_seconds: float | None = None,
    support_mode: str = "baseplate",
    hollow: bool = True,
    placement_strategy: str | None = None,
    instruction_mode: str | None = None,
) -> dict[str, Any]:
    """用现有隔离环境或 uv 运行 GPL 后端，保留领域退出状态。"""
    if quality not in {"fast", "balanced", "exhaustive", "direct"}:
        raise ValueError("quality 必须是 fast、balanced、exhaustive 或 direct")
    if support_mode not in {"baseplate", "table"}:
        raise ValueError("support_mode 必须是 baseplate 或 table")
    project = Path(project_dir).resolve()
    local_executable = project / ".venv" / (
        "Scripts/legolization.exe" if os.name == "nt" else "bin/legolization"
    )
    if local_executable.is_file():
        command = [str(local_executable)]
    elif shutil.which("uv"):
        command = ["uv", "run", "--project", str(project), "legolization"]
    else:
        raise RuntimeError(
            f"找不到 legolization 可执行文件：{local_executable}；请先安装 uv 并配置 {project}"
        )
    command.extend([
        "bundle",
        str(Path(input_path).resolve()),
        "--output",
        str(Path(output_dir).resolve()),
        "--quality",
        quality,
        "--render",
        "off",
        "--set",
        f"stability.support={support_mode}",
        "--set",
        f"geometry.hollow={'true' if hollow else 'false'}",
        "--set",
        "output.emit_support=false",
        "--json",
    ])
    if placement_strategy is not None:
        if placement_strategy not in {"fast", "bond", "greedy"}:
            raise ValueError("placement_strategy 必须是 legolization 的 fast、bond 或 greedy")
        command.extend(("--set", f"placement.strategy={placement_strategy}"))
    if instruction_mode is not None:
        if instruction_mode not in {"layer", "smart"}:
            raise ValueError("instruction_mode 必须是 layer 或 smart")
        command.extend(("--set", f"instructions.options.mode={instruction_mode}",
                        "--set", "instructions.options.stability_policy=warn"))
        if instruction_mode == "layer":
            command.extend(("--set", "instructions.options.subassemblies=false"))
    if duration_seconds is not None:
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("duration_seconds 必须大于 0")
        command.extend(("--duration", str(duration_seconds)))
        if placement_strategy is not None:
            command.extend(("--set", f"placement.time_budget_s={duration_seconds}"))
    elif quality == "exhaustive":
        raise ValueError("exhaustive 必须显式指定 duration_seconds")
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=solver_environment(),
    )
    # 2/3/4 是上游定义的可解析领域结果（不可拼、部分完成、精确求解上限），
    # 只有 1 等操作性错误才应在适配器边界抛异常。
    if completed.returncode not in {0, 2, 3, 4}:
        detail = completed.stderr.strip() or completed.stdout.strip()
        if "MemoryError:" in detail or "std::bad_alloc" in detail:
            raise RuntimeError("legolization 求解内存不足，请减小 LEGO 宽度后重试") from MemoryError(detail)
        raise RuntimeError(
            f"legolization 失败（exit={completed.returncode}）: {detail}"
        )
    payload: dict[str, Any] = {
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "output_dir": str(Path(output_dir).resolve()),
    }
    try:
        payload["result"] = json.loads(completed.stdout)
    except json.JSONDecodeError:
        pass
    return payload


def run_layout_preview(volume: VoxelVolume, output_dir: Path, project_dir: Path, budget: float, color: int) -> dict:
    """Run single-pass layout in the backend environment, with an independent wall-clock limit."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source = export_legolization_npy(volume, output_dir / "legolization_input.npy", color, preserve_unconstrained=True)
    model = output_dir / "layout_preview.ldr"
    native = project_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if native.is_file():
        command = [str(native)]
    elif shutil.which("uv"):
        command = ["uv", "run", "--project", str(project_dir), "python"]
    else:
        raise RuntimeError("没有找到 legolization 的 Python 环境")
    # Isolate imports so the worker loads the installed backend, not this adapter's filename.
    command += ["-I", str(Path(__file__).with_name("layout_preview_worker.py")), str(source.resolve()), str(model.resolve()), str(budget)]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace", env=solver_environment()) as process:
        try:
            stdout, stderr = process.communicate(timeout=budget)
        except subprocess.TimeoutExpired as exc:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5)
            if process.poll() is None:
                process.kill()
            process.communicate()
            raise RuntimeError(f"快速布局在 {budget:g} 秒内未返回，请增加预算或降低 studs") from exc
    if process.returncode or not model.is_file():
        raise RuntimeError("快速布局失败：" + (stderr.strip() or stdout.strip())[-1800:])
    return dict(json.loads(stdout.splitlines()[-1]), mode="layout-preview", placement_strategy="fast",
                model_path=str(model), buildable=None, stable=None, instructions_status="未执行")
