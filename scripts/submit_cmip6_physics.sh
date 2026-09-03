#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_CMIP6_RUN_ROOT:-$ATLAS_ROOT/.cmip6-runs/mpi-esm1-2-hr-production}"
STATIC_FILE="$ATLAS_ROOT/data/cmip6-inventory/era5-common-static-1deg.nc"
CONCURRENCY="${LPS_CMIP6_PHYSICS_CONCURRENCY:-80}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
mkdir -p "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"

FINAL_IDS=()
RUN_MANIFESTS=()

while IFS=$'\t' read -r INDEX SOURCE_LABEL TRACKING_ROOT LINK_ROOT; do
  PERIOD_ROOT="$(dirname "$TRACKING_ROOT")"
  DATA_ROOT="$PERIOD_ROOT/data"
  OUTPUT_ROOT="$PERIOD_ROOT/physics"
  LINKED="$TRACKING_ROOT/$SOURCE_LABEL-parallel-linked.csv"
  MANIFEST="$($PYTHON -m cmip6_pipeline.physics prepare \
    --linked "$LINKED" --output-root "$OUTPUT_ROOT" --source-label "$SOURCE_LABEL" | tail -1)"
  COUNT="$($PYTHON -c 'import pandas as pd,sys; print(len(pd.read_csv(sys.argv[1])))' "$MANIFEST")"
  LAST=$((COUNT - 1))
  PHYSICS_ID="$(sbatch --parsable --array="0-$LAST%$CONCURRENCY" \
    scripts/cmip6_physics_month.slurm "$MANIFEST" "$DATA_ROOT" "$OUTPUT_ROOT" "$STATIC_FILE")"
  FINAL_ID="$(sbatch --parsable --dependency="afterok:$PHYSICS_ID" \
    scripts/cmip6_physics_finalize.slurm "$MANIFEST" "$OUTPUT_ROOT" "$PERIOD_ROOT/period-plan.json")"
  FINAL_IDS+=("$FINAL_ID")
  RUN_MANIFESTS+=("$OUTPUT_ROOT/summary/manifest.json")
  printf '%s\t%s\t%s\t%s\n' "$SOURCE_LABEL" "$COUNT" "$PHYSICS_ID" "$FINAL_ID"
done < "$RUN_ROOT/link.tsv"

if [[ "${#FINAL_IDS[@]}" -eq 2 ]]; then
  PAIR_ID="$(sbatch --parsable --dependency="afterok:${FINAL_IDS[0]}:${FINAL_IDS[1]}" \
    scripts/cmip6_pair_summary.slurm "${RUN_MANIFESTS[0]}" "${RUN_MANIFESTS[1]}" "$RUN_ROOT/climate-summary")"
	printf 'paired-summary\t%s\n' "$PAIR_ID"
  PUBLISH_ID="$(sbatch --parsable --dependency="afterok:$PAIR_ID" \
    scripts/cmip6_publish_pair.slurm "${RUN_MANIFESTS[0]}" "${RUN_MANIFESTS[1]}" \
    "$RUN_ROOT/climate-summary/manifest.json" "$RUN_ROOT/climate-public")"
  printf 'publication-stage\t%s\n' "$PUBLISH_ID"
fi
