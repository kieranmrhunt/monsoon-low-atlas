#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --job-name=mla-cmip6-link-period
#SBATCH --output=hpc-logs/mla-cmip6-link-period-%A_%a.out
#SBATCH --error=hpc-logs/mla-cmip6-link-period-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?period-link job table is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX SOURCE_LABEL TRACKING_ROOT LINK_ROOT <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.production link-period \
  --source-label "$SOURCE_LABEL" --output-root "$TRACKING_ROOT" \
  --run-root "$LINK_ROOT" --workers "${LPS_CMIP6_LINK_WORKERS:-1}"
