#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --job-name=mla-rean-plan
#SBATCH --output=hpc-logs/mla-rean-plan-%j.out
#SBATCH --error=hpc-logs/mla-rean-plan-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
SOURCE="${1:?source is required}"
OUTPUT_ROOT="${2:?tracking output root is required}"
RUN_ROOT="${3:?parallel-link run root is required}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.parallel_link prepare \
  --source "$SOURCE" --output-root "$OUTPUT_ROOT" --run-root "$RUN_ROOT"
