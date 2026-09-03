#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --time=02:00:00
#SBATCH --job-name=mla-cmip6-link
#SBATCH --output=hpc-logs/mla-cmip6-link-%A_%a.out
#SBATCH --error=hpc-logs/mla-cmip6-link-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?link job table is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX TRACKING_ROOT SOURCE_LABEL <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.track link \
  --output-root "$TRACKING_ROOT" --source-label "$SOURCE_LABEL"
