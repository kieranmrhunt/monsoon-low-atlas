#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MAX_ACTIVE="${LPS_TIGGE_MAX_ACTIVE:-4}"
TIME_LIMIT="${LPS_TIGGE_TIME_LIMIT:-12:00:00}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/tigge-ecmwf-backfill-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"
CANARY_PLAN="$RUN_ROOT/canary-plan.json"
CANARY_JOBS="$RUN_ROOT/canary-jobs.tsv"
CANARY_CYCLE="${LPS_TIGGE_CANARY_CYCLE:-2016070100}"
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

# Require one known archived case to complete at the full provider horizon
# before releasing the large array. Once that case is public at +360 h, later
# submissions skip this gate automatically.
"$PYTHON" -m forecast_pipeline.plan_tigge_archive \
  --manifest "$OUTPUT/manifest.json" \
  --output "$CANARY_PLAN" \
  --jobs "$CANARY_JOBS" \
  --cycles "$CANARY_CYCLE"
CANARY_COUNT="$(wc -l < "$CANARY_JOBS")"
DEPENDENCY_ARGS=()
CANARY_MESSAGE="canary already complete"
if [[ "$CANARY_COUNT" != "0" ]]; then
  CANARY_ID="$(sbatch --parsable --time="$TIME_LIMIT" --array="1-1%1" scripts/backfill_forecast_cycle.slurm "$CANARY_JOBS" "$RUN_ROOT/canary" tigge "$OUTPUT")"
  DEPENDENCY_ARGS+=(--dependency="afterok:$CANARY_ID")
  CANARY_MESSAGE="canary $CANARY_ID ($CANARY_CYCLE to +360 h)"
fi

# ECDS applies tighter and workload-dependent per-user limits than the retired
# Web API. Keep only four cycles active; queue-limit rejections are retried with
# backoff inside each task rather than consuming the rest of the Slurm array.
ARRAY_ID="$(sbatch --parsable "${DEPENDENCY_ARGS[@]}" --time="$TIME_LIMIT" --array="1-$COUNT%$MAX_ACTIVE" scripts/backfill_forecast_cycle.slurm "$JOBS" "$RUN_ROOT" tigge "$OUTPUT")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$ARRAY_ID" scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT" tigge)"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm "$RUN_ROOT" "$OUTPUT" tigge tigge_backfill "$PLAN")"
echo "Submitted ECMWF TIGGE $CANARY_MESSAGE; array $ARRAY_ID ($COUNT tasks; up to $MAX_ACTIVE active), batching publisher $PUBLISH_ID and finalizer $FINAL_ID"
