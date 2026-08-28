#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
CATALOGUE="${LPS_WEATHER_CATALOGUE:-$ATLAS_ROOT/../lps-v5.3-continuity-framework/production/v5.6/public-release/lps_v5.6-era5-1940-2025-core.parquet}"
OUTPUT="${LPS_WEATHER_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-weather-v5.6-r1}"
OLD_GENERAL="${LPS_WEATHER_OLD_GENERAL:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-weather-v5.4.2-r2}"
OLD_PRECIP="${LPS_WEATHER_OLD_PRECIP:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-weather-v5.4.2-r5}"
MANIFEST="$ATLAS_ROOT/data/v56-weather-active-months.csv"
LOG_DIR="$OUTPUT/logs"

if [[ ! -f "$CATALOGUE" ]]; then
  echo "Missing passing v5.6 public catalogue: $CATALOGUE" >&2
  exit 1
fi
mkdir -p "$OUTPUT" "$LOG_DIR"
for field in vorticity rh500; do
  mkdir -p "$OUTPUT/$field"
  rsync -a --link-dest="$OLD_GENERAL/$field" "$OLD_GENERAL/$field/" "$OUTPUT/$field/"
done
mkdir -p "$OUTPUT/precipitation"
rsync -a --link-dest="$OLD_PRECIP/precipitation" "$OLD_PRECIP/precipitation/" "$OUTPUT/precipitation/"

cd "$ATLAS_ROOT"
"$PYTHON" scripts/build_vorticity_videos.py \
  --catalogue "$CATALOGUE" \
  --write-month-manifest "$MANIFEST" \
  --output-dir "$OUTPUT"
TASK_COUNT="$($PYTHON -c "import pandas as pd; print(len(pd.read_csv('$MANIFEST')))")"
LAST_TASK=$((TASK_COUNT - 1))

declare -A BUILD_JOBS
declare -A FINAL_JOBS
for field in vorticity precipitation rh500; do
  BUILD_JOBS[$field]=$(sbatch --parsable \
    --job-name="mla56-${field}" \
    --array="0-${LAST_TASK}" \
    --time=02:00:00 \
    --output="$LOG_DIR/${field}-%A_%a.out" \
    --error="$LOG_DIR/${field}-%A_%a.err" \
    scripts/build_vorticity_videos.slurm "$MANIFEST" "$OUTPUT" "$field")
  FINAL_JOBS[$field]=$(sbatch --parsable \
    --account=ncas_climate \
    --partition=standard \
    --qos=standard \
    --job-name="mla56-${field}-final" \
    --dependency="afterok:${BUILD_JOBS[$field]}" \
    --time=02:00:00 \
    --mem=8G \
    --output="$LOG_DIR/${field}-final-%j.out" \
    --error="$LOG_DIR/${field}-final-%j.err" \
    --wrap="cd '$ATLAS_ROOT' && '$PYTHON' scripts/build_vorticity_videos.py --field '$field' --month-manifest '$MANIFEST' --output-dir '$OUTPUT' --finalize")
done

RECORD="$ATLAS_ROOT/data/v56-weather-jobs.tsv"
{
  printf 'active_months\t%s\n' "$TASK_COUNT"
  printf 'field\tbuild_job\tfinalize_job\n'
  for field in vorticity precipitation rh500; do
    printf '%s\t%s\t%s\n' "$field" "${BUILD_JOBS[$field]}" "${FINAL_JOBS[$field]}"
  done
} > "$RECORD"
cat "$RECORD"
