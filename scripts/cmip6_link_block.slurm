#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --time=24:00:00
#SBATCH --job-name=mla-cmip6-link-block
#SBATCH --output=hpc-logs/mla-cmip6-link-block-%A_%a.out
#SBATCH --error=hpc-logs/mla-cmip6-link-block-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?parallel-link run root is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.parallel_link worker \
  --run-root "$RUN_ROOT" --task-id "${SLURM_ARRAY_TASK_ID:?array task ID is required}"
