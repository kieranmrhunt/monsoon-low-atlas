#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=high
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=05:30:00
#SBATCH --job-name=mla-tigge-probe
#SBATCH --output=hpc-logs/mla-tigge-probe-%A_%a.out
#SBATCH --error=hpc-logs/mla-tigge-probe-%A_%a.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
MODEL="${1:?TIGGE model is required}"
CYCLE="${2:?cycle is required}"
OUTPUT_ROOT="${3:?output root is required}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"

case "${SLURM_ARRAY_TASK_ID:?array task ID is required}" in
  1) COMPONENT="pressure" ;;
  2) COMPONENT="surface" ;;
  3) COMPONENT="precipitation" ;;
  *) echo "TIGGE component task must be 1, 2 or 3" >&2; exit 2 ;;
esac

cd "$ATLAS_ROOT"
exec "$PYTHON" -m forecast_pipeline.probe_tigge \
  --model "$MODEL" \
  --cycle "$CYCLE" \
  --component "$COMPONENT" \
  --output "$OUTPUT_ROOT/$MODEL/$CYCLE/$COMPONENT.grib"
