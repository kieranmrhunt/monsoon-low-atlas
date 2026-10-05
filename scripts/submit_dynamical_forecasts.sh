#!/bin/bash
# Latest: bash scripts/submit_dynamical_forecasts.sh recent
# Archive: bash scripts/submit_dynamical_forecasts.sh archive 2025-07-03 2025-07-21
set -euo pipefail
ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
TARGET="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MODE="${1:-recent}"
[[ "$MODE" == recent || "$MODE" == archive ]] || exit 2
cd "$ATLAS_ROOT"
mkdir -p .forecast-runs hpc-logs
exec 9>".forecast-runs/dynamical-$MODE-submit.lock"
/usr/bin/flock -n 9 || exit 0
QUEUED="$(timeout 30 squeue -h -u "$USER" -o '%j')"
if [[ "$MODE" == recent ]] && [[ "$QUEUED" == *mla-dyn-recent* ]]; then
  echo "Daily GEFS update already running."
  exit 0
fi
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/dynamical-$MODE-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RUN_ROOT"
ARGS=(--mode "$MODE" --manifest "$TARGET/manifest.json" --output "$RUN_ROOT/plan.json" --jobs "$RUN_ROOT/jobs.tsv")
if [[ "$MODE" == recent ]]; then
  ARGS+=(--models gefs-extended)
else
  ARGS+=(--models "${LPS_DYNAMICAL_MODELS:-aifs,gefs-extended}" --start "${2:?start date required}" --end "${3:?end date required}")
fi
"$PYTHON" -m forecast_pipeline.plan_dynamical "${ARGS[@]}"
COUNT="$(wc -l < "$RUN_ROOT/jobs.tsv")"
if [[ "$COUNT" == 0 ]]; then echo "Dynamical $MODE window is complete."; exit 0; fi
export LPS_FORECAST_WORKERS="${LPS_DYNAMICAL_WORKERS:-4}"
ARRAY_ID="$(sbatch --parsable --qos=high --cpus-per-task=4 --mem=24G --time=05:30:00 \
  --job-name="mla-dyn-$MODE" --array="1-$COUNT%${LPS_DYNAMICAL_CONCURRENCY:-64}" \
  --output="hpc-logs/mla-dyn-$MODE-%A_%a.out" --error="hpc-logs/mla-dyn-$MODE-%A_%a.err" \
  scripts/backfill_forecast_cycle.slurm "$RUN_ROOT/jobs.tsv" "$RUN_ROOT" "$MODE" "$TARGET")"
if [[ "$MODE" == recent ]]; then
  FINAL_ID="$(sbatch --parsable --job-name=mla-dyn-recent-final --dependency="afterany:$ARRAY_ID" \
    scripts/finalize_forecast_recent_backfill.slurm "$RUN_ROOT" "$RUN_ROOT/plan.json" "$TARGET")"
else
  FINAL_ID="$(sbatch --parsable --job-name=mla-dyn-archive-final --dependency="afterany:$ARRAY_ID" \
    scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$RUN_ROOT/plan.json" "$TARGET" archive)"
fi
echo "Dynamical $MODE array $ARRAY_ID ($COUNT cycles), finalizer $FINAL_ID; $RUN_ROOT"
