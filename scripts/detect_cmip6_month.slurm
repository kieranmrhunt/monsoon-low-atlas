#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --time=08:00:00
#SBATCH --job-name=mla-cmip6-detect
#SBATCH --output=hpc-logs/mla-cmip6-detect-%A_%a.out
#SBATCH --error=hpc-logs/mla-cmip6-detect-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?detection job table is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX MONTH DATA_ROOT TRACKING_ROOT <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ && "$MONTH" =~ ^[0-9]{6}$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.track detect-month \
  --data-root "$DATA_ROOT" --output-root "$TRACKING_ROOT" --month "$MONTH"
