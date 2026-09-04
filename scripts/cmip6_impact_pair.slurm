#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --job-name=mla-cmip6-impact-pair
#SBATCH --output=hpc-logs/mla-cmip6-impact-pair-%A_%a.out
#SBATCH --error=hpc-logs/mla-cmip6-impact-pair-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?impact-pair job table is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX HISTORICAL FUTURE OUTPUT_DIR <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.impact pair \
  --historical-manifest "$HISTORICAL" --future-manifest "$FUTURE" --output-dir "$OUTPUT_DIR"
