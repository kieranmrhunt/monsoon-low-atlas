#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=05:30:00
#SBATCH --job-name=mla-forecast
#SBATCH --output=hpc-logs/mla-forecast-%j.out
#SBATCH --error=hpc-logs/mla-forecast-%j.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
cd "$ATLAS_ROOT"
exec /usr/bin/bash scripts/run_forecast_update.sh
