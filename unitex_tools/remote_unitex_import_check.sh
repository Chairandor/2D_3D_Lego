#!/usr/bin/env bash
set -euo pipefail

WORK="${UNITEX_REMOTE_ROOT:-/data/unitex-work}"
ENV="$WORK/envs/unitex"

export PATH="$ENV/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export CUDA_HOME="$ENV"
export CUDA_VISIBLE_DEVICES=1
export HF_HOME="$WORK/cache/huggingface"
export HF_HUB_CACHE="$WORK/cache/huggingface/hub"
export XDG_CACHE_HOME="$WORK/cache/xdg"
export PYTHONPATH="$WORK/UniTEX"

cd "$WORK/UniTEX"
"$ENV/bin/python" - <<'PY'
import torch
import nvdiffrast.torch as dr
import pipeline

print("pipeline_import=ok")
print("pipeline_file", pipeline.__file__)
print("visible_gpus", torch.cuda.device_count())
if torch.cuda.is_available():
    free, total = torch.cuda.mem_get_info(0)
    print("gpu", torch.cuda.get_device_name(0))
    print("gpu_free_gib", round(free / 2**30, 2), "gpu_total_gib", round(total / 2**30, 2))
PY

/usr/bin/nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free --format=csv,noheader
