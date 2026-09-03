#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"

while IFS= read -r job_name; do
  case "$job_name" in
    mla-fc-recent|mla-fc-recent-final)
      echo "A rolling forecast-cycle repair is already queued or running; no duplicate submitted."
      exit 0
      ;;
  esac
done < <(timeout 30 squeue -h -u "$USER" -o '%j')

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/recent-backfill-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"

"$PYTHON" -m forecast_pipeline.plan_recent \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS" \
  --exclude-model aigefs

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "The rolling 72-hour forecast-cycle window is complete."
  exit 0
fi

CONCURRENCY="${LPS_FORECAST_RECENT_CONCURRENCY:-24}"
ARRAY_ID="$(sbatch --parsable --job-name=mla-fc-recent \
  --array="1-$COUNT%$CONCURRENCY" \
  scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" recent "$OUTPUT")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" \
  scripts/finalize_forecast_recent_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT")"
echo "Submitted rolling 72-hour repair array $ARRAY_ID ($COUNT cycles) and finalizer $FINAL_ID"
