#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
CACHE_ROOT="${LPS_CMA_TIGGE_CACHE:-$ATLAS_ROOT/.forecast-cache/cma-tigge}"
RECOVERY_PLAN="${LPS_CMA_TIGGE_RECOVERY_PLAN:?Set LPS_CMA_TIGGE_RECOVERY_PLAN to the submitted CMA plan}"
MAX_ACTIVE="${LPS_CMA_TIGGE_PROCESS_MAX_ACTIVE:-8}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/cma-tigge-processing-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.plan_cma_cache_processing \
  --cache-root "$CACHE_ROOT" \
  --recovery-plan "$RECOVERY_PLAN" \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "No complete unpublished CMA cycle caches are ready."
  exit 0
fi
export LPS_CMA_TIGGE_CACHE="$CACHE_ROOT"
ARRAY_ID="$(sbatch --parsable --array="1-$COUNT%$MAX_ACTIVE" scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" tigge "$OUTPUT")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT" tigge)"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm "$RUN_ROOT" "$OUTPUT" tigge tigge_cma_recovery "$PLAN")"
echo "Submitted CMA cache processing array $ARRAY_ID ($COUNT cycles), publisher $PUBLISH_ID and finalizer $FINAL_ID"
