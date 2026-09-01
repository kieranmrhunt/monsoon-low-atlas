#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=05:30:00
#SBATCH --job-name=mla-fc-operational-worker
#SBATCH --output=hpc-logs/mla-forecast-%j.out
#SBATCH --error=hpc-logs/mla-forecast-%j.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
MODEL="${1:?forecast model is required}"
OUTPUT="${2:?staging output directory is required}"
PUBLISH_TARGET="${3:-}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
export LPS_FORECAST_MODELS="$MODEL"
export LPS_FORECAST_OUT="$OUTPUT"
cd "$ATLAS_ROOT"
/usr/bin/bash scripts/run_forecast_update.sh

# Publish each completed model immediately instead of making fast deterministic
# guidance wait for the slowest ensemble. The shared manifest lock serializes
# concurrent model completions; the finalizer still performs the full audit and
# removes staging once every model job has ended.
if [[ -n "$PUBLISH_TARGET" ]]; then
  "$PYTHON" -m forecast_pipeline.merge_runs \
    --target "$PUBLISH_TARGET" \
    --run-root "$(dirname "$OUTPUT")" \
    --partial \
    --keep-staging
fi
