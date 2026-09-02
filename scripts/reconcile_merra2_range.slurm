#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --job-name=mla-m2-ledger
#SBATCH --output=hpc-logs/mla-m2-ledger-%j.out
#SBATCH --error=hpc-logs/mla-m2-ledger-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?complete day table is required}"
DATA_ROOT="${2:?MERRA-2 data root is required}"
LEDGER="${3:?MERRA-2 ledger path is required}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.merra2 \
  --root "$DATA_ROOT" --ledger "$LEDGER" \
  reconcile-table --jobs "$JOBS"
