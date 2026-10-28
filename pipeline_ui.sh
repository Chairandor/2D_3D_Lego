#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export TMPDIR="${TMPDIR:-$PWD/.runtime/tmp}"
export TORCH_EXTENSIONS_DIR="${TORCH_EXTENSIONS_DIR:-$PWD/.runtime/extensions}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$PWD/.runtime/cache}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$PWD/.runtime/config}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$PWD/.runtime/data}"
if [[ -d .runtime/ui-deps ]]; then
    export PYTHONPATH="$PWD/.runtime/ui-deps${PYTHONPATH:+:$PYTHONPATH}"
fi
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export DIFFUSERS_OFFLINE="${DIFFUSERS_OFFLINE:-1}"
mkdir -p "$TMPDIR" "$TORCH_EXTENSIONS_DIR" "$XDG_CACHE_HOME"
exec "${UI_PYTHON:-$PWD/.venv-hunyuan/bin/python}" -u pipeline_ui.py --no-browser "$@"
