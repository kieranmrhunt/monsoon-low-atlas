#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
CATALOGUE="${LPS_COMPOSITE_CATALOGUE:-$ATLAS_ROOT/../lps-v5.3-continuity-framework/production/v5.5.1/public-release/lps_v5.5.1-era5-1940-2025-core.parquet}"
OUTPUT="${LPS_COMPOSITE_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-composites-v5.5.1-r1}"
CONCURRENCY="${LPS_COMPOSITE_CONCURRENCY:-200}"
mkdir -p "$OUTPUT/tracks" "$OUTPUT/logs"
TRACK_COUNT="$($PYTHON -c "import pandas as pd; print(pd.read_parquet('$CATALOGUE', columns=['track_id']).track_id.nunique())")"
LAST_INDEX=$((TRACK_COUNT - 1))
cd "$ATLAS_ROOT"
LPS_COMPOSITE_OUT="$OUTPUT" LPS_COMPOSITE_CATALOGUE="$CATALOGUE" \
  sbatch \
    --array="0-${LAST_INDEX}%${CONCURRENCY}" \
    --output="$OUTPUT/logs/%A_%a.out" \
    --error="$OUTPUT/logs/%A_%a.err" \
    scripts/build_storm_composites.slurm
