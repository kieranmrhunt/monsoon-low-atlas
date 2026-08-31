#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MAX_ACTIVE="${LPS_GRAPHCAST_ARCHIVE_MAX_ACTIVE:-16}"
TIME_LIMIT="${LPS_GRAPHCAST_ARCHIVE_TIME_LIMIT:-04:00:00}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/graphcast-noaa-backfill-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"
INVENTORY="$RUN_ROOT/noaa-inventory.json"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$($PYTHON -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"

if [[ ! "$MAX_ACTIVE" =~ ^[1-9][0-9]*$ ]]; then
  echo "LPS_GRAPHCAST_ARCHIVE_MAX_ACTIVE must be a positive integer" >&2
  exit 2
fi

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.plan_graphcast_archive \
  --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS" \
  --fetch-inventory \
  --save-inventory "$INVENTORY"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "NOAA/CIRA GraphCast archive backfill is already complete."
  exit 0
fi

ARRAY_ID="$(sbatch --parsable --qos=high --time="$TIME_LIMIT" \
  --array="1-$COUNT%$MAX_ACTIVE" --job-name=mla-graphcast-arc \
  --output=hpc-logs/mla-graphcast-arc-%A_%a.out \
  --error=hpc-logs/mla-graphcast-arc-%A_%a.err \
  scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" archive "$OUTPUT")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" \
  scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT" archive)"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm \
  "$RUN_ROOT" "$OUTPUT" archive graphcast_noaa_archive "$PLAN")"

echo "Submitted GraphCast archive array $ARRAY_ID ($COUNT cycles; up to $MAX_ACTIVE active), progressive publisher $PUBLISH_ID and finalizer $FINAL_ID"
echo "Run root: $RUN_ROOT"
