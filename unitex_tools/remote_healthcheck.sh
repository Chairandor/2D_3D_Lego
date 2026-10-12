#!/usr/bin/env bash
set -u

WORK="${UNITEX_REMOTE_ROOT:-/data/unitex-work}"
PY="$WORK/envs/unitex/bin/python"

"$PY" - <<'PY'
import torch
import transformers
import diffusers
import bitsandbytes
import open3d
import xatlas
import rembg

print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
print("transformers", transformers.__version__)
print("diffusers", diffusers.__version__)
print("bitsandbytes", bitsandbytes.__version__)
print("open3d", open3d.__version__)
if torch.cuda.is_available():
    print("gpu0", torch.cuda.get_device_name(0))
PY

echo "COMPILERS"
/usr/bin/gcc --version | /usr/bin/head -n 1 || true
/usr/bin/g++ --version | /usr/bin/head -n 1 || true
"$WORK/envs/unitex/bin/nvcc" --version | /usr/bin/tail -n 1
/usr/bin/df -h /data | /usr/bin/tail -n 1
