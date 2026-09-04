#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=04:00:00
#SBATCH --job-name=mla-cmip6-physics-dispatch
#SBATCH --output=hpc-logs/mla-cmip6-physics-dispatch-%j.out
#SBATCH --error=hpc-logs/mla-cmip6-physics-dispatch-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:-${LPS_CMIP6_RUN_ROOT:-$ATLAS_ROOT/.cmip6-runs/mpi-esm1-2-hr-production}}"
export LPS_CMIP6_RUN_ROOT="$RUN_ROOT"
cd "$ATLAS_ROOT"
exec bash scripts/submit_cmip6_physics.sh
