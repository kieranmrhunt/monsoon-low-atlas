#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --job-name=mla-cmip6-pair-summary
#SBATCH --output=hpc-logs/mla-cmip6-pair-summary-%j.out
#SBATCH --error=hpc-logs/mla-cmip6-pair-summary-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
HISTORICAL="${1:?historical summary manifest is required}"
FUTURE="${2:?future summary manifest is required}"
OUTPUT_ROOT="${3:?paired summary output root is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.summarise pair \
  --historical-manifest "$HISTORICAL" --future-manifest "$FUTURE" --output-dir "$OUTPUT_ROOT"
