#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=high
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:20:00
#SBATCH --job-name=mla-tigge-submit
#SBATCH --output=hpc-logs/mla-tigge-submit-%j.out
#SBATCH --error=hpc-logs/mla-tigge-submit-%j.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?TIGGE staging root is required}"
PLAN="${2:?TIGGE plan is required}"
PUBLIC_ROOT="${3:?public forecast root is required}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"

cd "$ATLAS_ROOT"
exec "$PYTHON" -m forecast_pipeline.submit_tigge_requests \
  --plan "$PLAN" \
  --run-root "$RUN_ROOT" \
  --public-root "$PUBLIC_ROOT" \
  --capacity "${LPS_TIGGE_REMOTE_CAPACITY:-20}" \
  --max-submissions "${LPS_TIGGE_SUBMISSIONS_PER_PUMP:-20}" \
  --initial-cursor "${LPS_TIGGE_INITIAL_CURSOR:-809}"
