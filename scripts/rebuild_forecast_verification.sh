#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MANIFEST="$OUTPUT/archive-manifest.json.gz"
SUMMARY="$OUTPUT/verification-summary.json.gz"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"

mkdir -p "$ATLAS_ROOT/.forecast-runs" "$ATLAS_ROOT/hpc-logs"
exec 9>"$ATLAS_ROOT/.forecast-runs/verification-refresh.lock"
if ! /usr/bin/flock -n 9; then
  echo "A forecast-verification refresh is already active."
  exit 0
fi

if [[ ! -f "$MANIFEST" ]]; then
  echo "Missing archive manifest: $MANIFEST" >&2
  exit 1
fi
if [[ -f "$SUMMARY" && "$SUMMARY" -nt "$MANIFEST" ]]; then
  echo "Forecast verification is already current."
  exit 0
fi
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

cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.archive_verification \
  --archive-root "$OUTPUT" \
  --core "$CORE" \
  --output "$SUMMARY"
chmod 0644 "$SUMMARY"
