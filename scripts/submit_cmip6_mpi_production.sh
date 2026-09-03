#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_CMIP6_RUN_ROOT:-$ATLAS_ROOT/.cmip6-runs/mpi-esm1-2-hr-production}"
CONCURRENCY="${LPS_CMIP6_CONCURRENCY:-80}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
STATIC_SOURCE="$ATLAS_ROOT/../lps-v5.3-continuity-framework/inputs/era5_static_SA/era5_invariants_0p25_SA.nc"
STATIC_FILE="$ATLAS_ROOT/data/cmip6-inventory/era5-common-static-1deg.nc"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"

if [[ -s "$RUN_ROOT/standardise.job-id" && "${LPS_CMIP6_FORCE_SUBMIT:-0}" != "1" ]]; then
  printf 'A CMIP6 production submission is already recorded in %s; set LPS_CMIP6_FORCE_SUBMIT=1 to submit a resumable retry.\n' "$RUN_ROOT"
  exit 0
fi

cd "$ATLAS_ROOT"
if [[ ! -s "$STATIC_FILE" ]]; then
  "$PYTHON" -m cmip6_pipeline.static --source "$STATIC_SOURCE" --output "$STATIC_FILE"
fi
"$PYTHON" -m cmip6_pipeline.plan \
  --run-root "$RUN_ROOT" --static-file "$STATIC_FILE" mpi-paired

STANDARD_COUNT="$(wc -l < "$RUN_ROOT/standardise.tsv")"
BOUNDARY_COUNT="$(wc -l < "$RUN_ROOT/aux-boundary.tsv")"
DETECT_COUNT="$(wc -l < "$RUN_ROOT/detect.tsv")"
LINK_COUNT="$(wc -l < "$RUN_ROOT/link.tsv")"

STANDARD_ID="$(sbatch --parsable --array="1-$STANDARD_COUNT%$CONCURRENCY" \
  scripts/standardise_cmip6_month.slurm "$RUN_ROOT/standardise.tsv")"
BOUNDARY_ID="$(sbatch --parsable --array="1-$BOUNDARY_COUNT%$BOUNDARY_COUNT" \
  scripts/standardise_cmip6_aux_boundary.slurm "$RUN_ROOT/aux-boundary.tsv")"
DETECT_ID="$(sbatch --parsable --dependency="afterok:$STANDARD_ID:$BOUNDARY_ID" \
  --array="1-$DETECT_COUNT%$CONCURRENCY" \
  scripts/detect_cmip6_month.slurm "$RUN_ROOT/detect.tsv")"
LINK_ID="$(sbatch --parsable --dependency="afterok:$DETECT_ID" \
  --array="1-$LINK_COUNT%$LINK_COUNT" \
  scripts/link_cmip6_period.slurm "$RUN_ROOT/link.tsv")"
PHYSICS_ID="$(sbatch --parsable --dependency="afterok:$LINK_ID" \
  scripts/dispatch_cmip6_physics.slurm "$RUN_ROOT")"

printf '%s\n' "$STANDARD_ID" > "$RUN_ROOT/standardise.job-id"
printf '%s\n' "$BOUNDARY_ID" > "$RUN_ROOT/aux-boundary.job-id"
printf '%s\n' "$DETECT_ID" > "$RUN_ROOT/detect.job-id"
printf '%s\n' "$LINK_ID" > "$RUN_ROOT/link.job-id"
printf '%s\n' "$PHYSICS_ID" > "$RUN_ROOT/physics-dispatch.job-id"
printf 'CMIP6 MPI production: standardise %s, boundary %s, detect %s, link %s, physics dispatch %s\n' \
  "$STANDARD_ID" "$BOUNDARY_ID" "$DETECT_ID" "$LINK_ID" "$PHYSICS_ID"
