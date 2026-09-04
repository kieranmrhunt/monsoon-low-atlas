#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO="${1:-ssp245}"
RUN_ROOT="${LPS_CMIP6_GWL_ROOT:-$ATLAS_ROOT/.cmip6-runs/gwl-$SCENARIO}"
CONCURRENCY="${LPS_CMIP6_CONCURRENCY:-80}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
STATIC_FILE="$ATLAS_ROOT/data/cmip6-inventory/era5-common-static-1deg.nc"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"

cd "$ATLAS_ROOT"
"$PYTHON" -m cmip6_pipeline.gwl \
  --output-root "$RUN_ROOT" --static-file "$STATIC_FILE" --scenario "$SCENARIO" \
  --level 1.5 --level 2.0

while IFS=$'\t' read -r RUN_ID SOURCE MEMBER EXPERIMENT LEVEL CENTRAL PERIOD_ROOT; do
	if [[ "$RUN_ID" == "run_id" ]]; then
		continue
	fi
  if [[ -s "$PERIOD_ROOT/standardise.job-id" && "${LPS_CMIP6_FORCE_SUBMIT:-0}" != "1" ]]; then
    printf '%s already submitted as %s\n' "$RUN_ID" "$(head -1 "$PERIOD_ROOT/standardise.job-id")"
    continue
  fi
  STANDARD_COUNT="$(wc -l < "$PERIOD_ROOT/standardise.tsv")"
  DETECT_COUNT="$(wc -l < "$PERIOD_ROOT/detect.tsv")"
  STANDARD_ID="$(sbatch --parsable --array="1-$STANDARD_COUNT%$CONCURRENCY" \
    scripts/standardise_cmip6_month.slurm "$PERIOD_ROOT/standardise.tsv")"
  DETECT_ID="$(sbatch --parsable --dependency="afterok:$STANDARD_ID" --array="1-$DETECT_COUNT%$CONCURRENCY" \
    scripts/detect_cmip6_month.slurm "$PERIOD_ROOT/detect.tsv")"
  DISPATCH_ID="$(sbatch --parsable --dependency="afterok:$DETECT_ID" \
    scripts/dispatch_cmip6_parallel_link.slurm "$PERIOD_ROOT")"
  printf '%s\n' "$STANDARD_ID" > "$PERIOD_ROOT/standardise.job-id"
  printf '%s\n' "$DETECT_ID" > "$PERIOD_ROOT/detect.job-id"
  printf '%s\n' "$DISPATCH_ID" > "$PERIOD_ROOT/link-dispatch.job-id"
  printf '%s · %s °C (%s–%s): standardise %s, detect %s, dispatch %s\n' \
    "$SOURCE" "$LEVEL" "$((CENTRAL - 9))" "$((CENTRAL + 10))" "$STANDARD_ID" "$DETECT_ID" "$DISPATCH_ID"
done < "$RUN_ROOT/runs.tsv"
