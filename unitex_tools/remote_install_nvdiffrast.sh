#!/usr/bin/env bash
set -euo pipefail

WORK="${UNITEX_REMOTE_ROOT:-/data/unitex-work}"
ENV="$WORK/envs/unitex"

export PATH="$ENV/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export CUDA_HOME="$ENV"
export CC="$ENV/bin/x86_64-conda-linux-gnu-gcc"
export CXX="$ENV/bin/x86_64-conda-linux-gnu-g++"
export TORCH_CUDA_ARCH_LIST=8.0
export MAX_JOBS=4
export TMPDIR="$WORK/tmp"
mkdir -p "$TMPDIR"

"$ENV/bin/python" -m pip install \
  "$WORK/sources/nvdiffrast-main" \
  --no-build-isolation \
  --no-cache-dir

"$ENV/bin/python" - <<'PY'
import nvdiffrast
import nvdiffrast.torch as dr
print("nvdiffrast", nvdiffrast.__version__)
print("module", dr.__file__)
PY
