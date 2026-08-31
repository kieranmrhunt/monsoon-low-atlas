#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
START="${LPS_MOGREPS_START:-}"
END="${LPS_MOGREPS_END:-}"
AUDIT_WORKERS="${LPS_MOGREPS_AUDIT_WORKERS:-16}"
MAX_ACTIVE="${LPS_MOGREPS_MAX_ACTIVE:-12}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/mogreps-backfill-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
PLAN_ARGS=(
  --manifest "$OUTPUT/manifest.json"
  --output "$PLAN"
  --jobs "$JOBS"
  --workers "$AUDIT_WORKERS"
)
if [[ -n "$START" ]]; then
  PLAN_ARGS+=(--start "$START")
fi
if [[ -n "$END" ]]; then
  PLAN_ARGS+=(--end "$END")
fi
"$PYTHON" -m forecast_pipeline.plan_mogreps_archive "${PLAN_ARGS[@]}"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "The rolling MOGREPS-G archive is already captured."
  exit 0
fi

CANARY_ID="$(sbatch --parsable --array=1 scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" archive "$OUTPUT")"
ARRAY_IDS=("$CANARY_ID")
if (( COUNT > 1 )); then
  ARRAY_ID="$(sbatch --parsable --dependency="afterok:$CANARY_ID" --array="2-$COUNT%$MAX_ACTIVE" scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" archive "$OUTPUT")"
  ARRAY_IDS+=("$ARRAY_ID")
fi
DEPENDENCY="$(IFS=:; echo "${ARRAY_IDS[*]}")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$DEPENDENCY" scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT")"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm "$RUN_ROOT" "$OUTPUT" archive archive_backfill_mogreps_g "$PLAN")"
echo "Submitted MOGREPS-G canary $CANARY_ID and ${COUNT} total cycle tasks; publisher $PUBLISH_ID, finalizer $FINAL_ID"
