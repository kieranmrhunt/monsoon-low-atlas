#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:20:00
#SBATCH --job-name=mla-forecast
#SBATCH --output=hpc-logs/mla-forecast-final-%j.out
#SBATCH --error=hpc-logs/mla-forecast-final-%j.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?run staging root is required}"
OUTPUT="${2:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
MODE="${3:-full}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
ARGS=(--target "$OUTPUT" --run-root "$RUN_ROOT")
if [[ "$MODE" == "partial" ]]; then
  ARGS+=(--partial)
elif [[ "$MODE" != "full" ]]; then
  echo "Unknown finalization mode: $MODE (expected full or partial)" >&2
  exit 2
fi
exec "$PYTHON" -m forecast_pipeline.merge_runs "${ARGS[@]}"
