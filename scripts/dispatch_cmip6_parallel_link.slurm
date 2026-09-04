#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:30:00
#SBATCH --job-name=mla-cmip6-link-dispatch
#SBATCH --output=hpc-logs/mla-cmip6-link-dispatch-%j.out
#SBATCH --error=hpc-logs/mla-cmip6-link-dispatch-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?CMIP6 paired run root is required}"
cd "$ATLAS_ROOT"
exec bash scripts/submit_cmip6_parallel_link.sh "$RUN_ROOT"
