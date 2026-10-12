"""Read ignored local configuration; explicit environment variables take priority."""

from __future__ import annotations

import json
import os
from pathlib import Path


def load_project_config(root: Path) -> None:
    path = root / "config.local.json"
    if not path.is_file():
        return
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("config.local.json must be a JSON object")
    allowed = {"UNITEX_HOST", "UNITEX_SSH_KEY", "UNITEX_REMOTE_ROOT", "UNITEX_GPU", "HUNYUAN_PYTHON", "LEGACY_IMAGE_PYTHON"}
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"Unknown configuration option: {key}")
        if value is not None:
            os.environ.setdefault(key, str(value))
