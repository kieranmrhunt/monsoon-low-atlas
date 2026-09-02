#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=20G
#SBATCH --time=08:00:00
#SBATCH --job-name=mla-rean-detect
#SBATCH --output=hpc-logs/mla-rean-detect-%A_%a.out
#SBATCH --error=hpc-logs/mla-rean-detect-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
SOURCE="${1:?source is required}"
JOBS="${2:?month table is required}"
DATA_ROOT="${3:?standard data root is required}"
OUTPUT_ROOT="${4:?tracking output root is required}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX MONTH <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ && "$MONTH" =~ ^[0-9]{6}$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.track \
  --source "$SOURCE" --data-root "$DATA_ROOT" --output-root "$OUTPUT_ROOT" \
  detect-month --month "$MONTH"
