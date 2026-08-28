#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
CATALOGUE="${LPS_COMPOSITE_CATALOGUE:-$ATLAS_ROOT/../lps-v5.3-continuity-framework/production/v5.6/public-release/lps_v5.6-era5-1940-2025-core.parquet}"
OUTPUT="${LPS_COMPOSITE_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-composites-v5.6-r1}"
CONCURRENCY="${LPS_COMPOSITE_CONCURRENCY:-}"
mkdir -p "$OUTPUT/tracks" "$OUTPUT/logs"
TRACK_COUNT="$($PYTHON -c "import pandas as pd; print(pd.read_parquet('$CATALOGUE', columns=['track_id']).track_id.nunique())")"
LAST_INDEX=$((TRACK_COUNT - 1))
ARRAY_SPEC="0-${LAST_INDEX}"
if [[ -n "$CONCURRENCY" ]]; then
  ARRAY_SPEC="${ARRAY_SPEC}%${CONCURRENCY}"
fi
cd "$ATLAS_ROOT"
BUILD_JOB=$(LPS_COMPOSITE_OUT="$OUTPUT" LPS_COMPOSITE_CATALOGUE="$CATALOGUE" \
  sbatch --parsable \
    --array="$ARRAY_SPEC" \
    --output="$OUTPUT/logs/%A_%a.out" \
    --error="$OUTPUT/logs/%A_%a.err" \
    scripts/build_storm_composites.slurm)
MANIFEST_JOB=$(sbatch --parsable \
  --account=ncas_climate \
  --partition=standard \
  --qos=standard \
  --job-name=mla56-composite-final \
  --dependency="afterok:$BUILD_JOB" \
  --time=02:00:00 \
  --mem=16G \
  --output="$OUTPUT/logs/manifest-%j.out" \
  --error="$OUTPUT/logs/manifest-%j.err" \
  --wrap="cd '$ATLAS_ROOT' && '$PYTHON' scripts/build_storm_composite.py --catalogue '$CATALOGUE' --output '$OUTPUT' --manifest")
printf 'tracks\t%s\nbuild_job\t%s\nmanifest_job\t%s\n' "$TRACK_COUNT" "$BUILD_JOB" "$MANIFEST_JOB" | tee "$OUTPUT/jobs.tsv"
