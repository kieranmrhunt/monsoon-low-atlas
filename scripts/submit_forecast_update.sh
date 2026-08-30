#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ATLAS_ROOT/hpc-logs"

if [[ -n "$(squeue -h -u "$USER" -n mla-forecast -o '%A')" ]]; then
  echo "An mla-forecast update is already queued or running; no duplicate submitted."
  exit 0
fi

cd "$ATLAS_ROOT"
JOB_ID="$(sbatch --parsable scripts/update_forecasts.slurm)"
echo "Submitted forecast update job $JOB_ID"
