#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
if [[ ! -f "$BUILD_MANIFEST" ]]; then
  echo "Missing atlas build manifest: $BUILD_MANIFEST" >&2
  exit 1
fi
CORE_NAME="$("$PYTHON" -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"
CORE="${LPS_FORECAST_CORE:-$ATLAS_ROOT/assets/$CORE_NAME}"

if [[ ! -f "$CORE" ]]; then
  echo "Missing active atlas core: $CORE" >&2
  exit 1
fi

mkdir -p "$OUTPUT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m forecast_pipeline.update \
  --output-root "$OUTPUT" \
  --atlas-core "$CORE" \
  --cycle latest \
  --models gfs,gefs,ifs,ifs-ens,aifs,aifs-ens \
  --horizon 120 \
  --workers "${LPS_FORECAST_WORKERS:-${SLURM_CPUS_PER_TASK:-1}}"
