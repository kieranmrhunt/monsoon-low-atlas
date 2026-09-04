#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --job-name=mla-cmip6-link-merge
#SBATCH --output=hpc-logs/mla-cmip6-link-merge-%j.out
#SBATCH --error=hpc-logs/mla-cmip6-link-merge-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
SOURCE_LABEL="${1:?source label is required}"
OUTPUT_ROOT="${2:?tracking output root is required}"
RUN_ROOT="${3:?parallel-link run root is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.parallel_link merge \
  --source "$SOURCE_LABEL" --output-root "$OUTPUT_ROOT" --run-root "$RUN_ROOT"
