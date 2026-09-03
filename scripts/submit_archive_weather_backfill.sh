#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MODELS="${LPS_ARCHIVE_WEATHER_MODELS:-gfs,gefs,gefs-control,ifs,ukmo-global,graphcast-noaa,graphcast-ifs-noaa,mogreps-g}"
MAX_ACTIVE="${LPS_ARCHIVE_WEATHER_MAX_ACTIVE:-64}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/archive-weather-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.plan_archive_weather \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS" \
  --models "$MODELS"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "The selected non-TIGGE archive weather fields are already complete."
  exit 0
fi

ARRAY_ID="$(sbatch --parsable --array="1-$COUNT%$MAX_ACTIVE" \
  scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" archive "$OUTPUT")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" \
  scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT")"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm \
  "$RUN_ROOT" "$OUTPUT" archive archive_weather_backfill "$PLAN")"
echo "Submitted archive-weather array $ARRAY_ID ($COUNT cycles), publisher $PUBLISH_ID and finalizer $FINAL_ID"
