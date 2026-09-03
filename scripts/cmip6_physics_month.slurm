#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --time=04:00:00
#SBATCH --job-name=mla-cmip6-physics
#SBATCH --output=hpc-logs/mla-cmip6-physics-%A_%a.out
#SBATCH --error=hpc-logs/mla-cmip6-physics-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
MANIFEST="${1:?monthly physics manifest is required}"
DATA_ROOT="${2:?standardized data root is required}"
OUTPUT_ROOT="${3:?physics output root is required}"
STATIC_FILE="${4:?common static file is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.physics worker \
  --manifest "$MANIFEST" --task-id "$SLURM_ARRAY_TASK_ID" \
  --data-root "$DATA_ROOT" --output-root "$OUTPUT_ROOT" --static-file "$STATIC_FILE"
