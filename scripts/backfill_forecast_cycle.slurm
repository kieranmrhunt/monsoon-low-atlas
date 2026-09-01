#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=05:30:00
#SBATCH --job-name=mla-fc-backfill
#SBATCH --output=hpc-logs/mla-fc-backfill-%A_%a.out
#SBATCH --error=hpc-logs/mla-fc-backfill-%A_%a.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?job table is required}"
RUN_ROOT="${2:?staging root is required}"
MODE="${3:?recent or archive mode is required}"
PUBLISH_TARGET="${4:-}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX MODEL CYCLE HORIZON FIRST_STEP <<< "$ROW"
[[ -n "${INDEX:-}" && -n "${MODEL:-}" && -n "${CYCLE:-}" ]]
if [[ ! "$MODEL" =~ ^[a-z0-9-]+$ || ! "$CYCLE" =~ ^[0-9]{10}$ ]]; then
  echo "Invalid model/cycle in job table: $MODEL $CYCLE" >&2
  exit 2
fi
HORIZON="${HORIZON:-available}"
FIRST_STEP="${FIRST_STEP:-0}"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$("$PYTHON" -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"
OUTPUT="$RUN_ROOT/${MODEL}-${CYCLE}"
RAW_CACHE_FILE=""
if [[ "$MODE" == "tigge" ]]; then
  # Keep a validated raw ECDS cycle if processing or publication is
  # interrupted. A rerun can then skip the expensive tape staging step.
  export LPS_TIGGE_DOWNLOAD_CACHE="${LPS_TIGGE_DOWNLOAD_CACHE:-$RUN_ROOT/raw-tigge}"
  RAW_CACHE_FILE="$LPS_TIGGE_DOWNLOAD_CACHE/$MODEL/$CYCLE/all.grib"
fi
ARGS=()
if [[ "$MODE" == "archive" ]]; then
  ARGS+=(--archive-only)
elif [[ "$MODE" == "tigge" ]]; then
  ARGS+=(--archive-only --archive-collection tigge)
elif [[ "$MODE" != "recent" ]]; then
  echo "Unknown backfill mode: $MODE (expected recent, archive or tigge)" >&2
  exit 2
fi

cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.update \
  --output-root "$OUTPUT" \
  --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" \
  --cycle "$CYCLE" \
  --models "$MODEL" \
  --horizon "$HORIZON" \
  --first-step "$FIRST_STEP" \
  --workers "${LPS_FORECAST_WORKERS:-8}" \
  "${ARGS[@]}"

if [[ -n "$PUBLISH_TARGET" && "$MODE" != "recent" ]]; then
  "$PYTHON" -m forecast_pipeline.merge_archives \
    --target "$PUBLISH_TARGET" \
    --collection "$MODE" \
    --nonblocking-lock \
    "$OUTPUT"
fi

# Successful processing has a complete compact cycle asset, so retain the raw
# ECDS file only when a task fails and might benefit from a cheap retry.
if [[ -n "$RAW_CACHE_FILE" ]]; then
  rm -f "$RAW_CACHE_FILE"
  rmdir "$(dirname "$RAW_CACHE_FILE")" 2>/dev/null || true
  rmdir "$(dirname "$(dirname "$RAW_CACHE_FILE")")" 2>/dev/null || true
fi
