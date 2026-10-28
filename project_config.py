"""Read ignored local configuration; explicit environment variables take priority."""

from __future__ import annotations

import json
import os
from pathlib import Path


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
    allowed = {"UNITEX_HOST", "UNITEX_SSH_KEY", "UNITEX_REMOTE_ROOT", "UNITEX_GPU", "UNITEX_EXECUTION", "HUNYUAN_PYTHON", "LEGACY_IMAGE_PYTHON", "LEGO_PYTHON", "LEGO_CLI"}
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"Unknown configuration option: {key}")
        if key == "UNITEX_EXECUTION" and value not in {"local", "ssh"}:
            raise ValueError("UNITEX_EXECUTION must be local or ssh")
        if value is not None:
            os.environ.setdefault(key, str(value))
