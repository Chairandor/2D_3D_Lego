"""Read ignored local configuration; explicit environment variables take priority."""

from __future__ import annotations

import json
import os
from pathlib import Path


def resolve_core_root(root: Path) -> Path:
    """定位显式配置、已有相邻核心或仓库内附带的三维源码。"""
    if value := os.environ.get("LEGOLIZER_ROOT"):
        path = Path(value)
        return (path if path.is_absolute() else root / path).resolve()
    for candidate in (root.parent, root):
        if (candidate / "src/legolizer").is_dir():
            return candidate.resolve()
    return root.parent.resolve()


def venv_executable(env_dir: Path, name: str) -> Path:
    return env_dir / ("Scripts" if os.name == "nt" else "bin") / (name + ".exe" if os.name == "nt" else name)


def terminate_process_tree(process: object) -> None:
    """Stop only a job launched in its own session, including its GPU children."""
    import signal
    import subprocess

    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass


def load_project_config(root: Path) -> None:
    path = root / "config.local.json"
    if not path.is_file():
        return
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("config.local.json must be a JSON object")
    allowed = {
        "UNITEX_HOST",
        "UNITEX_SSH_KEY",
        "UNITEX_REMOTE_ROOT",
        "UNITEX_GPU",
        "HUNYUAN_PYTHON",
        "LEGACY_IMAGE_PYTHON",
        "PROXE_ROOT",
        "PROXE_ENV_SCRIPT",
        "PROXE_RELAY_CONFIG",
        "PROXE_GPU",
        "LDRAW_DATA_HOME",
        "LDRAW_CACHE_HOME",
        "EXAMPLE_RUNS_ROOT",
        "DASHSCOPE_API_KEY",
        "INSTRUCTIONS_PYTHON",
        "INSTRUCTIONS_LEOCAD_BIN",
        "INSTRUCTIONS_XVFB_BIN",
        "UNITEX_EXECUTION",
        "LEGO_PYTHON",
        "LEGO_CLI",
        "ARUCO_ROOT",
        "ARUCO_PYTHON",
        "ARUCO_STUDIO_COLORS",
        "LEGOLIZER_ROOT",
        "PROCESSING3D_PYTHON",
        "LDRAW_LIBRARY",
    }
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"Unknown configuration option: {key}")
        if key == "UNITEX_EXECUTION" and value not in {"local", "ssh"}:
            raise ValueError("UNITEX_EXECUTION must be local or ssh")
        if value is not None:
            os.environ.setdefault(key, str(value))

    # pyldraw3 follows the XDG directory convention rather than an app-specific
    # environment variable. Keep the friendly project config names above and
    # translate them once for this process and every child process it starts.
    if value := os.environ.get("LDRAW_DATA_HOME"):
        os.environ.setdefault("XDG_DATA_HOME", value)
    if value := os.environ.get("LDRAW_CACHE_HOME"):
        os.environ.setdefault("XDG_CACHE_HOME", value)
