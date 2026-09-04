#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --time=04:00:00
#SBATCH --job-name=mla-era5-1deg
#SBATCH --output=hpc-logs/mla-era5-1deg-%A_%a.out
#SBATCH --error=hpc-logs/mla-era5-1deg-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?standardisation job table is required}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX MONTH OUTPUT_ROOT SOURCE_ROOT BADC_ROOT STATIC_FILE SP_SOURCE <<< "$ROW"
[[ "$INDEX" =~ ^[0-9]+$ && "$MONTH" =~ ^[0-9]{6}$ ]]
cd "$ATLAS_ROOT"
exec "$PYTHON" -m cmip6_pipeline.standardise_era5 \
  --output-root "$OUTPUT_ROOT" --source-root "$SOURCE_ROOT" \
  --badc-root "$BADC_ROOT" --static-file "$STATIC_FILE" \
  --surface-pressure-source "$SP_SOURCE" --month "$MONTH"
