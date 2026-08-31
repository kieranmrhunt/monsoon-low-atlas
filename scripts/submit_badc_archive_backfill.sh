#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/badc-ukmo-backfill-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.plan_badc_archive \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "BADC Met Office archive backfill is already complete."
  exit 0
fi

ARRAY_ID="$(sbatch --parsable --array="1-$COUNT" scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" archive "$OUTPUT")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT")"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm "$RUN_ROOT" "$OUTPUT" archive archive_backfill_badc_ukmo "$PLAN")"
echo "Submitted BADC Met Office array $ARRAY_ID ($COUNT tasks), batching publisher $PUBLISH_ID and finalizer $FINAL_ID"
