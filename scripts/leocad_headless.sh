#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$task_root/.venv-hunyuan/bin/python" "$task_root/scripts/leocad_headless.py" "$@"
