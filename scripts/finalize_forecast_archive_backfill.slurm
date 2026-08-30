#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --job-name=mla-fc-archive-final
#SBATCH --output=hpc-logs/mla-fc-archive-final-%j.out
#SBATCH --error=hpc-logs/mla-fc-archive-final-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?staging root is required}"
PLAN="${2:?plan is required}"
OUTPUT="${3:?public output is required}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m forecast_pipeline.merge_archives --target "$OUTPUT" --run-root "$RUN_ROOT" --plan "$PLAN" --cleanup
