#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=02:00:00
#SBATCH --job-name=mla-merra2-standard
#SBATCH --output=hpc-logs/mla-merra2-standard-%j.out
#SBATCH --error=hpc-logs/mla-merra2-standard-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
DATA_ROOT="${1:?MERRA-2 data root is required}"
MONTH="${2:?month YYYYMM is required}"
PRECIP_ROOT="${3:-/home/users/kieran/ncas/data/MERRA-2}"
MONTH_DASHED="${MONTH:0:4}-${MONTH:4:2}"
LEDGER="$DATA_ROOT/opendap-ledger.json"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
"$PYTHON" -m reanalysis_pipeline.merra2 \
  --root "$DATA_ROOT" --ledger "$LEDGER" \
  reconcile-month --month "$MONTH_DASHED"
"$PYTHON" -m reanalysis_pipeline.standardise_merra2 \
  --root "$DATA_ROOT" --precip-root "$PRECIP_ROOT" \
  standardise-month --month "$MONTH"
exec "$PYTHON" -m reanalysis_pipeline.standardise_merra2 \
  --root "$DATA_ROOT" --precip-root "$PRECIP_ROOT" \
  validate-month --month "$MONTH"
