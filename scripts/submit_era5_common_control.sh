#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_ERA5_CONTROL_ROOT:-$ATLAS_ROOT/.cmip6-runs/era5-common-grid-control}"
CONCURRENCY="${LPS_ERA5_CONTROL_CONCURRENCY:-120}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
STATIC_FILE="$ATLAS_ROOT/data/cmip6-inventory/era5-common-static-1deg.nc"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"

if [[ -s "$RUN_ROOT/standardise.job-id" && "${LPS_ERA5_CONTROL_FORCE_SUBMIT:-0}" != "1" ]]; then
  printf 'An ERA5 common-grid submission is already recorded in %s; set LPS_ERA5_CONTROL_FORCE_SUBMIT=1 for a resumable retry.\n' "$RUN_ROOT"
  exit 0
fi

cd "$ATLAS_ROOT"
"$PYTHON" -m cmip6_pipeline.era5_control --run-root "$RUN_ROOT" --static-file "$STATIC_FILE"
STANDARD_COUNT="$(wc -l < "$RUN_ROOT/standardise.tsv")"
DETECT_COUNT="$(wc -l < "$RUN_ROOT/detect.tsv")"
STANDARD_ID="$(sbatch --parsable --array="1-$STANDARD_COUNT%$CONCURRENCY" \
  scripts/standardise_era5_common_month.slurm "$RUN_ROOT/standardise.tsv")"
DETECT_ID="$(sbatch --parsable --dependency="afterok:$STANDARD_ID" \
  --array="1-$DETECT_COUNT%$CONCURRENCY" scripts/detect_cmip6_month.slurm "$RUN_ROOT/detect.tsv")"
LINK_DISPATCH_ID="$(sbatch --parsable --dependency="afterok:$DETECT_ID" \
  scripts/dispatch_cmip6_parallel_link.slurm "$RUN_ROOT")"

printf '%s\n' "$STANDARD_ID" > "$RUN_ROOT/standardise.job-id"
printf '%s\n' "$DETECT_ID" > "$RUN_ROOT/detect.job-id"
printf '%s\n' "$LINK_DISPATCH_ID" > "$RUN_ROOT/link-dispatch.job-id"
printf 'ERA5 common-grid control: standardise %s, detect %s, annual-link dispatch %s\n' \
  "$STANDARD_ID" "$DETECT_ID" "$LINK_DISPATCH_ID"
