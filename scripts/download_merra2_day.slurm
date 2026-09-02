#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=02:00:00
#SBATCH --job-name=mla-merra2-day
#SBATCH --output=hpc-logs/mla-merra2-day-%A_%a.out
#SBATCH --error=hpc-logs/mla-merra2-day-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?job table is required}"
DATA_ROOT="${2:?MERRA-2 data root is required}"
LEDGER="${3:?MERRA-2 ledger path is required}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX DAY <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ && "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.merra2 \
  --root "$DATA_ROOT" \
  --ledger "$LEDGER" \
  download-day --date "$DAY"
