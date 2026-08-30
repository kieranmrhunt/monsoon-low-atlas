#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MAX_ACTIVE="${LPS_TIGGE_MAX_ACTIVE:-16}"
TIME_LIMIT="${LPS_TIGGE_TIME_LIMIT:-12:00:00}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/tigge-ecmwf-backfill-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$($PYTHON -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"

if [[ ! "$MAX_ACTIVE" =~ ^[1-9][0-9]*$ ]]; then
  echo "LPS_TIGGE_MAX_ACTIVE must be a positive integer" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.plan_tigge_archive \
  --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "ECMWF TIGGE archive backfill is already complete."
  exit 0
fi

# ECMWF documents a 20-request per-user queue ceiling. Leave four slots free
# for interactive/retry work while keeping the TIGGE tape-staging queue full.
ARRAY_ID="$(sbatch --parsable --time="$TIME_LIMIT" --array="1-$COUNT%$MAX_ACTIVE" scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" tigge "$OUTPUT")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT" tigge)"
echo "Submitted ECMWF TIGGE array $ARRAY_ID ($COUNT tasks; up to $MAX_ACTIVE active, publishing each completed cycle) and finalizer $FINAL_ID"
