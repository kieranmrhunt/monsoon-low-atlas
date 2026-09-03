#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=08:00:00
#SBATCH --job-name=mla-cmip6-physics-final
#SBATCH --output=hpc-logs/mla-cmip6-physics-final-%j.out
#SBATCH --error=hpc-logs/mla-cmip6-physics-final-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
MANIFEST="${1:?monthly physics manifest is required}"
OUTPUT_ROOT="${2:?physics output root is required}"
PERIOD_PLAN="${3:-}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
MERGED="$($PYTHON -m cmip6_pipeline.physics merge --manifest "$MANIFEST" --output-root "$OUTPUT_ROOT" | tail -1)"
FINAL="$($PYTHON -m cmip6_pipeline.physics finalize --input "$MERGED" --output-root "$OUTPUT_ROOT" | tail -1)"
if [[ -n "$PERIOD_PLAN" ]]; then
  QA_REPORT="$OUTPUT_ROOT/qa-summary.json"
  "$PYTHON" -m cmip6_pipeline.qa \
    --catalogue "$FINAL" --period-plan "$PERIOD_PLAN" --output "$QA_REPORT"
  exec "$PYTHON" -m cmip6_pipeline.summarise run-from-plan \
    --catalogue "$FINAL" --period-plan "$PERIOD_PLAN" --output-dir "$OUTPUT_ROOT/summary" \
    --qa-report "$QA_REPORT"
fi
printf '%s\n' "$FINAL"
