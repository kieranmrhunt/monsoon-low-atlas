#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --job-name=mla-rean-diag
#SBATCH --output=hpc-logs/mla-rean-diag-%j.out
#SBATCH --error=hpc-logs/mla-rean-diag-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
SOURCE="${1:?source is required}"
LINKED="${2:?linked tracks are required}"
OUTPUT="${3:?native output directory is required}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.native --source "$SOURCE" --linked "$LINKED" --output "$OUTPUT"
