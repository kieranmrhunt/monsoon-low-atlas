#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MODELS="${LPS_TIGGE_MULTICENTRE_MODELS:-tigge-bom,tigge-cma,tigge-cptec,tigge-dwd,tigge-eccc,tigge-imd,tigge-jma,tigge-kma,tigge-mf,tigge-ncep,tigge-ncmrwf,tigge-ukmo}"
START="${LPS_TIGGE_MULTICENTRE_START:-2006100100}"
END="${LPS_TIGGE_MULTICENTRE_END:-2025123112}"
MAX_ACTIVE="${LPS_TIGGE_MULTICENTRE_MAX_ACTIVE:-4}"
CANARY_ACTIVE="${LPS_TIGGE_MULTICENTRE_CANARY_ACTIVE:-1}"
TIME_LIMIT="${LPS_TIGGE_TIME_LIMIT:-12:00:00}"
CHUNK_SIZE="${LPS_TIGGE_ARRAY_CHUNK_SIZE:-9000}"
AFTER_JOB="${LPS_TIGGE_MULTICENTRE_AFTER_JOB:-}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/tigge-multicentre-backfill-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"
CANARY_PLAN="$RUN_ROOT/canary-plan.json"
CANARY_JOBS="$RUN_ROOT/canary-jobs.tsv"
CONSTRAINTS="$RUN_ROOT/ecds-constraints.json"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$($PYTHON -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"

for VALUE in "$MAX_ACTIVE" "$CANARY_ACTIVE" "$CHUNK_SIZE"; do
  if [[ ! "$VALUE" =~ ^[1-9][0-9]*$ ]]; then
    echo "TIGGE concurrency and chunk settings must be positive integers" >&2
    exit 2
  fi
done
if (( CHUNK_SIZE > 9999 )); then
  echo "LPS_TIGGE_ARRAY_CHUNK_SIZE must not exceed JASMIN MaxArraySize-1 (9999)" >&2
  exit 2
fi

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"

# Fetch the catalogue constraints once, pin them with the run, and use the same
# snapshot for the canaries and the full plan.
"$PYTHON" -m forecast_pipeline.plan_tigge_archive \
  --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" \
  --manifest "$OUTPUT/manifest.json" \
  --output "$CANARY_PLAN" \
  --jobs "$CANARY_JOBS" \
  --models "$MODELS" \
  --start "$START" \
  --end "$END" \
  --fetch-constraints \
  --save-constraints "$CONSTRAINTS" \
  --one-per-model latest \
  --manifest-key tigge_multicentre_canaries

"$PYTHON" -m forecast_pipeline.plan_tigge_archive \
  --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS" \
  --models "$MODELS" \
  --start "$START" \
  --end "$END" \
  --constraints "$CONSTRAINTS" \
  --manifest-key tigge_multicentre_backfill

CANARY_COUNT="$(wc -l < "$CANARY_JOBS")"
COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "Multi-centre TIGGE archive backfill is already complete."
  exit 0
fi
if [[ "$CANARY_COUNT" == "0" ]]; then
  CANARY_DEPENDENCY=""
  CANARY_MESSAGE="all centre canaries already public"
else
  CANARY_ID="$(sbatch --parsable --time="$TIME_LIMIT" --array="1-$CANARY_COUNT%$CANARY_ACTIVE" scripts/backfill_forecast_cycle.slurm "$CANARY_JOBS" "$RUN_ROOT/canary" tigge "$OUTPUT")"
  CANARY_DEPENDENCY="afterok:$CANARY_ID"
  CANARY_MESSAGE="canary array $CANARY_ID ($CANARY_COUNT centres)"
fi

split -l "$CHUNK_SIZE" -d -a 3 --additional-suffix=.tsv "$JOBS" "$RUN_ROOT/jobs-"
PREVIOUS=""
ARRAY_IDS=()
for CHUNK in "$RUN_ROOT"/jobs-[0-9]*.tsv; do
  CHUNK_COUNT="$(wc -l < "$CHUNK")"
  DEPENDENCIES=()
  if [[ -n "$PREVIOUS" ]]; then
    DEPENDENCIES+=(--dependency="afterany:$PREVIOUS")
  else
    FIRST_DEPENDENCY="$CANARY_DEPENDENCY"
    if [[ -n "$AFTER_JOB" ]]; then
      FIRST_DEPENDENCY="${FIRST_DEPENDENCY:+$FIRST_DEPENDENCY,}afterany:$AFTER_JOB"
    fi
    if [[ -n "$FIRST_DEPENDENCY" ]]; then
      DEPENDENCIES+=(--dependency="$FIRST_DEPENDENCY")
    fi
  fi
  ARRAY_ID="$(sbatch --parsable "${DEPENDENCIES[@]}" --time="$TIME_LIMIT" --array="1-$CHUNK_COUNT%$MAX_ACTIVE" scripts/backfill_forecast_cycle.slurm "$CHUNK" "$RUN_ROOT" tigge "$OUTPUT")"
  ARRAY_IDS+=("$ARRAY_ID")
  PREVIOUS="$ARRAY_ID"
done

FINAL_ID="$(sbatch --parsable --dependency="afterany:$PREVIOUS" scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT" tigge)"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm "$RUN_ROOT" "$OUTPUT" tigge tigge_multicentre_backfill "$PLAN")"
echo "Submitted $CANARY_MESSAGE; multi-centre arrays ${ARRAY_IDS[*]} ($COUNT model-cycles; up to $MAX_ACTIVE active), batching publisher $PUBLISH_ID and finalizer $FINAL_ID"
