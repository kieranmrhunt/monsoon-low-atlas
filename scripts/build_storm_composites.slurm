#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --time=04:00:00
#SBATCH --mem=8G

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
PYTHON="${LPS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_COMPOSITE_OUT:?LPS_COMPOSITE_OUT is required}"
CATALOGUE="${LPS_COMPOSITE_CATALOGUE:-$ATLAS_ROOT/../lps-v5.3-continuity-framework/production/v5.6/public-release/lps_v5.6-era5-1940-2025-core.parquet}"
TASK_CACHE="${TMPDIR:-/tmp}/lps_composite_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
export MPLCONFIGDIR="$TASK_CACHE/matplotlib"
export XDG_CACHE_HOME="$TASK_CACHE/xdg"
mkdir -p "$MPLCONFIGDIR" "$XDG_CACHE_HOME" "$OUTPUT/tracks"
cd "$ATLAS_ROOT"
EXTRA_ARGS=()
if [[ "${LPS_COMPOSITE_FORCE:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--force)
fi
if [[ "${LPS_COMPOSITE_RH_ONLY:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--relative-humidity-only)
fi
"$PYTHON" scripts/build_storm_composite.py \
  --catalogue "$CATALOGUE" \
  --output "$OUTPUT" \
  --array-index "$SLURM_ARRAY_TASK_ID" \
  "${EXTRA_ARGS[@]}"
