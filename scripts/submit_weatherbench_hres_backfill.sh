#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MAX_ACTIVE="${LPS_WEATHERBENCH_HRES_MAX_ACTIVE:-80}"
TIME_LIMIT="${LPS_WEATHERBENCH_HRES_TIME_LIMIT:-00:30:00}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/weatherbench-hres-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"
CANARY_JOBS="$RUN_ROOT/canary-jobs.tsv"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$("$PYTHON" -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"

if [[ ! "$MAX_ACTIVE" =~ ^[1-9][0-9]*$ ]]; then
  echo "LPS_WEATHERBENCH_HRES_MAX_ACTIVE must be a positive integer" >&2
  exit 2
fi

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.plan_weatherbench_archive \
  --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "WeatherBench IFS HRES archive backfill is already complete."
  exit 0
fi

sed -n '1p' "$JOBS" > "$CANARY_JOBS"
CANARY_ID="$(sbatch --parsable --qos=high --cpus-per-task=4 --mem=8G --time="$TIME_LIMIT" --array=1 scripts/backfill_forecast_cycle.slurm "$CANARY_JOBS" "$RUN_ROOT/canary" archive)"
ARRAY_ID="$(sbatch --parsable --dependency="afterok:$CANARY_ID" --qos=high --cpus-per-task=4 --mem=8G --time="$TIME_LIMIT" --array="1-$COUNT%$MAX_ACTIVE" scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" archive "$OUTPUT")"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm "$RUN_ROOT" "$OUTPUT" archive weatherbench_hres_archive "$PLAN")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT" archive)"
echo "Submitted WeatherBench HRES canary $CANARY_ID, array $ARRAY_ID ($COUNT cycles), batching publisher $PUBLISH_ID and finalizer $FINAL_ID"
