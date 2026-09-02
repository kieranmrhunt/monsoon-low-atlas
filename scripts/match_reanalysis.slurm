#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --job-name=mla-rean-match
#SBATCH --output=hpc-logs/mla-rean-match-%j.out
#SBATCH --error=hpc-logs/mla-rean-match-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
SOURCE="${1:?source is required}"
LINKED="${2:?linked track file is required}"
OUTPUT="${3:?compact output asset is required}"
ERA5="${4:-$ATLAS_ROOT/../lps-v5.3-continuity-framework/production/v5.6/public-release/lps_v5.6-era5-1940-2025-core.parquet}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.match \
  --source "$SOURCE" --linked "$LINKED" --era5 "$ERA5" --output "$OUTPUT"
