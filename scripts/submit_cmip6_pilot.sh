#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_CMIP6_PILOT_ROOT:-$ATLAS_ROOT/.cmip6-runs/mpi-esm1-2-hr-pilot}"
CONCURRENCY="${LPS_CMIP6_CONCURRENCY:-12}"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"

STANDARD_JOBS="$RUN_ROOT/standardise.tsv"
DETECT_JOBS="$RUN_ROOT/detect.tsv"
LINK_JOBS="$RUN_ROOT/link.tsv"
HIST_ROOT="$RUN_ROOT/MPI-ESM1-2-HR_historical_r1i1p1f1_gn"
SSP_ROOT="$RUN_ROOT/MPI-ESM1-2-HR_ssp245_r1i1p1f1_gn"

if [[ ! -s "$STANDARD_JOBS" ]]; then
  index=1
  for month in 199005 199006 199007 199008 199009 199010; do
    printf '%s\tCMIP\tMPI-M\tMPI-ESM1-2-HR\thistorical\tr1i1p1f1\tgn\t%s\t%s\n' \
      "$index" "$month" "$HIST_ROOT/data" >> "$STANDARD_JOBS"
    index=$((index + 1))
  done
  for month in 208005 208006 208007 208008 208009 208010; do
    printf '%s\tScenarioMIP\tDKRZ\tMPI-ESM1-2-HR\tssp245\tr1i1p1f1\tgn\t%s\t%s\n' \
      "$index" "$month" "$SSP_ROOT/data" >> "$STANDARD_JOBS"
    index=$((index + 1))
  done
fi

if [[ ! -s "$DETECT_JOBS" ]]; then
  index=1
  for month in 199006 199007 199008 199009; do
    printf '%s\t%s\t%s\t%s\n' "$index" "$month" "$HIST_ROOT/data" "$HIST_ROOT/tracking" >> "$DETECT_JOBS"
    index=$((index + 1))
  done
  for month in 208006 208007 208008 208009; do
    printf '%s\t%s\t%s\t%s\n' "$index" "$month" "$SSP_ROOT/data" "$SSP_ROOT/tracking" >> "$DETECT_JOBS"
    index=$((index + 1))
  done
fi

if [[ ! -s "$LINK_JOBS" ]]; then
  printf '1\t%s\tMPI-ESM1-2-HR_historical_r1i1p1f1_gn\n' "$HIST_ROOT/tracking" >> "$LINK_JOBS"
  printf '2\t%s\tMPI-ESM1-2-HR_ssp245_r1i1p1f1_gn\n' "$SSP_ROOT/tracking" >> "$LINK_JOBS"
fi

cd "$ATLAS_ROOT"
STANDARD_COUNT="$(wc -l < "$STANDARD_JOBS")"
DETECT_COUNT="$(wc -l < "$DETECT_JOBS")"
STANDARD_ID="$(sbatch --parsable --array="1-$STANDARD_COUNT%$CONCURRENCY" \
  scripts/standardise_cmip6_month.slurm "$STANDARD_JOBS")"
DETECT_ID="$(sbatch --parsable --dependency="afterok:$STANDARD_ID" --array="1-$DETECT_COUNT%$CONCURRENCY" \
  scripts/detect_cmip6_month.slurm "$DETECT_JOBS")"
LINK_ID="$(sbatch --parsable --dependency="afterok:$DETECT_ID" --array="1-2%2" \
  scripts/link_cmip6_pilot.slurm "$LINK_JOBS")"
printf '%s\n' "$STANDARD_ID" > "$RUN_ROOT/standardise.job-id"
printf '%s\n' "$DETECT_ID" > "$RUN_ROOT/detect.job-id"
printf '%s\n' "$LINK_ID" > "$RUN_ROOT/link.job-id"
printf 'CMIP6 pilot: standardise %s, detect %s, link %s\n' "$STANDARD_ID" "$DETECT_ID" "$LINK_ID"
