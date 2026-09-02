#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=03:00:00
#SBATCH --job-name=mla-rean-track
#SBATCH --output=hpc-logs/mla-rean-track-%j.out
#SBATCH --error=hpc-logs/mla-rean-track-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
SOURCE="${1:?source is required}"
DATA_ROOT="${2:?data root is required}"
OUTPUT_ROOT="${3:?tracking output root is required}"
MONTH="${4:?month YYYYMM is required}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.track \
  --source "$SOURCE" --data-root "$DATA_ROOT" --output-root "$OUTPUT_ROOT" \
  run-month --month "$MONTH"
