#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MODELS="${LPS_TIGGE_INDIA_MODELS:-tigge-imd,tigge-ncmrwf}"
START="${LPS_TIGGE_INDIA_START:-2006100100}"
END="${LPS_TIGGE_INDIA_END:-2025123112}"
MAX_ACTIVE="${LPS_TIGGE_INDIA_MAX_ACTIVE:-20}"
TIME_LIMIT="${LPS_TIGGE_TIME_LIMIT:-12:00:00}"
CHUNK_SIZE="${LPS_TIGGE_ARRAY_CHUNK_SIZE:-9000}"
QOS="${LPS_TIGGE_INDIA_QOS:-high}"
PINNED_CONSTRAINTS="${LPS_TIGGE_CONSTRAINTS:-$ATLAS_ROOT/.forecast-runs/tigge-multicentre-backfill-20260831T075456Z/ecds-constraints.json}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/tigge-india-priority-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
JOBS="$RUN_ROOT/jobs.tsv"
CONSTRAINTS="$RUN_ROOT/ecds-constraints.json"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$($PYTHON -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"

for VALUE in "$MAX_ACTIVE" "$CHUNK_SIZE"; do
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
PLAN_AVAILABILITY_ARGS=()
if [[ -f "$PINNED_CONSTRAINTS" ]]; then
  cp "$PINNED_CONSTRAINTS" "$CONSTRAINTS"
  PLAN_AVAILABILITY_ARGS+=(--constraints "$CONSTRAINTS")
else
  PLAN_AVAILABILITY_ARGS+=(--fetch-constraints --save-constraints "$CONSTRAINTS")
fi

"$PYTHON" -m forecast_pipeline.plan_tigge_archive \
  --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" \
  --manifest "$OUTPUT/manifest.json" \
  --output "$PLAN" \
  --jobs "$JOBS" \
  --models "$MODELS" \
  --start "$START" \
  --end "$END" \
  --manifest-key tigge_india_centres_priority \
  --job-order newest-first \
  "${PLAN_AVAILABILITY_ARGS[@]}"

COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "IMD/NCMRWF TIGGE priority backfill is already complete."
  exit 0
fi

# These two centres are deliberately submitted ahead of the chronological
# multi-centre queue. ECDS permits 20 queued requests per user and every TIGGE
# cycle now has one request containing both pressure/surface families and both
# control/perturbed types, so twenty simultaneous cycles fill the service queue
# without dropping detector inputs or ensemble members.
# Each task still retries transient queue responses with bounded backoff.
# ManifestLock makes concurrent progressive publication safe on JASMIN's shared
# filesystem.
split -l "$CHUNK_SIZE" -d -a 3 --additional-suffix=.tsv "$JOBS" "$RUN_ROOT/jobs-"
ARRAY_IDS=()
for CHUNK in "$RUN_ROOT"/jobs-[0-9]*.tsv; do
  CHUNK_COUNT="$(wc -l < "$CHUNK")"
  ARRAY_ID="$(sbatch --parsable --qos="$QOS" --cpus-per-task=8 --mem=24G --time="$TIME_LIMIT" \
    --array="1-$CHUNK_COUNT%$MAX_ACTIVE" --job-name=mla-tigge-india \
    --output=hpc-logs/mla-tigge-india-%A_%a.out \
    --error=hpc-logs/mla-tigge-india-%A_%a.err \
    scripts/backfill_forecast_cycle.slurm "$CHUNK" "$RUN_ROOT" tigge "$OUTPUT")"
  ARRAY_IDS+=("$ARRAY_ID")
done

DEPENDENCY="afterany:$(IFS=:; echo "${ARRAY_IDS[*]}")"
FINAL_ID="$(sbatch --parsable --dependency="$DEPENDENCY" \
  scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$OUTPUT" tigge)"
PUBLISH_ID="$(sbatch --parsable scripts/watch_forecast_archive_publish.slurm \
  "$RUN_ROOT" "$OUTPUT" tigge tigge_india_centres_priority "$PLAN")"

echo "Submitted IMD/NCMRWF TIGGE priority arrays ${ARRAY_IDS[*]} ($COUNT model-cycles; up to $MAX_ACTIVE active), progressive publisher $PUBLISH_ID and finalizer $FINAL_ID"
echo "Run root: $RUN_ROOT"
