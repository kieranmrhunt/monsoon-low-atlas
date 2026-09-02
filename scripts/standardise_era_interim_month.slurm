#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=20G
#SBATCH --time=08:00:00
#SBATCH --job-name=mla-erai-standard
#SBATCH --output=hpc-logs/mla-erai-standard-%A_%a.out
#SBATCH --error=hpc-logs/mla-erai-standard-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?month table is required}"
DATA_ROOT="${2:?ERA-Interim data root is required}"
ARCHIVE="${3:-/badc/ecmwf-era-interim/data}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX MONTH <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ && "$MONTH" =~ ^[0-9]{6}$ ]]
cd "$ATLAS_ROOT"
if "$PYTHON" -m reanalysis_pipeline.standardise_era_interim --root "$DATA_ROOT" --archive "$ARCHIVE" validate-month --month "$MONTH" >/dev/null 2>&1; then
	exit 0
fi
"$PYTHON" -m reanalysis_pipeline.standardise_era_interim \
  --root "$DATA_ROOT" --archive "$ARCHIVE" standardise-month --month "$MONTH"
exec "$PYTHON" -m reanalysis_pipeline.standardise_era_interim \
  --root "$DATA_ROOT" --archive "$ARCHIVE" validate-month --month "$MONTH"
