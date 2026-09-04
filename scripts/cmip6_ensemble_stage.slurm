#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --job-name=mla-cmip6-ensemble-stage
#SBATCH --output=hpc-logs/mla-cmip6-ensemble-stage-%j.out
#SBATCH --error=hpc-logs/mla-cmip6-ensemble-stage-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
OUTPUT_ROOT="${1:?ensemble staging output root is required}"
shift
[[ "$#" -ge 2 ]]
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ARGS=()
for MANIFEST in "$@"; do
  ARGS+=(--public-manifest "$MANIFEST")
done
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.summarise ensemble-index \
  "${ARGS[@]}" --output-dir "$OUTPUT_ROOT" --status multi-model-awaiting-review
