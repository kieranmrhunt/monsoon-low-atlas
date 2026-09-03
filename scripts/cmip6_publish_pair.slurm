#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00
#SBATCH --job-name=mla-cmip6-publish-stage
#SBATCH --output=hpc-logs/mla-cmip6-publish-stage-%j.out
#SBATCH --error=hpc-logs/mla-cmip6-publish-stage-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
HISTORICAL_MANIFEST="${1:?historical summary manifest is required}"
FUTURE_MANIFEST="${2:?future summary manifest is required}"
PAIR_MANIFEST="${3:?paired summary manifest is required}"
OUTPUT_ROOT="${4:?staging output root is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.summarise publish-pair \
  --historical-manifest "$HISTORICAL_MANIFEST" \
  --future-manifest "$FUTURE_MANIFEST" \
  --pair-manifest "$PAIR_MANIFEST" \
  --output-dir "$OUTPUT_ROOT"
