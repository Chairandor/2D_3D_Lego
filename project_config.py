"""Read ignored local configuration; explicit environment variables take priority."""

from __future__ import annotations

import json
import os
from pathlib import Path


def venv_executable(env_dir: Path, name: str) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / f"{name}.exe"
    return env_dir / "bin" / name


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
    }
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"Unknown configuration option: {key}")
        if value is not None:
            os.environ.setdefault(key, str(value))

    # pyldraw3 follows the XDG directory convention rather than an app-specific
    # environment variable. Keep the friendly project config names above and
    # translate them once for this process and every child process it starts.
    if value := os.environ.get("LDRAW_DATA_HOME"):
        os.environ.setdefault("XDG_DATA_HOME", value)
    if value := os.environ.get("LDRAW_CACHE_HOME"):
        os.environ.setdefault("XDG_CACHE_HOME", value)
