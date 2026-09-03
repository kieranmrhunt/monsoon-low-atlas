#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --time=08:00:00
#SBATCH --job-name=mla-cmip6-standard
#SBATCH --output=hpc-logs/mla-cmip6-standard-%A_%a.out
#SBATCH --error=hpc-logs/mla-cmip6-standard-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?standardisation job table is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX ACTIVITY INSTITUTION SOURCE EXPERIMENT MEMBER GRID MONTH OUTPUT_ROOT <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ && "$MONTH" =~ ^[0-9]{6}$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.standardise \
  --output-root "$OUTPUT_ROOT" \
  --activity "$ACTIVITY" --institution "$INSTITUTION" --source-id "$SOURCE" \
  --experiment-id "$EXPERIMENT" --member-id "$MEMBER" --grid-label "$GRID" \
  standardise-month --month "$MONTH"
