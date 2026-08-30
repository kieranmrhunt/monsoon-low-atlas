#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=23:50:00
#SBATCH --job-name=mla-fc-publish
#SBATCH --output=hpc-logs/mla-fc-publish-%j.out
#SBATCH --error=hpc-logs/mla-fc-publish-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?staging root is required}"
OUTPUT="${2:?public output is required}"
COLLECTION="${3:?archive collection is required}"
MANIFEST_KEY="${4:?backfill manifest key is required}"
PLAN="${5:?backfill plan is required}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m forecast_pipeline.watch_archive_publish \
  --target "$OUTPUT" \
  --run-root "$RUN_ROOT" \
  --collection "$COLLECTION" \
  --manifest-key "$MANIFEST_KEY" \
  --plan "$PLAN" \
  --poll-seconds 60
