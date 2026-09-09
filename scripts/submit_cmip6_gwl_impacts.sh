#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_CMIP6_GWL_IMPACT_ROOT:-$ATLAS_ROOT/.cmip6-runs/gwl-impact-production}"
GWL_ROOT="${LPS_CMIP6_GWL_ROOT:-$ATLAS_ROOT/.cmip6-runs/gwl-ssp245}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
GEOMETRY="$ATLAS_ROOT/assets/atlas-core.cefb51e2bde1.json.gz"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"

submit_with_retry() {
  local result=""
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if result="$(/usr/bin/timeout 30s /usr/bin/sbatch --parsable "$@")"; then
      printf '%s\n' "$result"
      return 0
    fi
    printf 'Slurm submission attempt %s/6 failed; retrying in 5 s.\n' "$attempt" >&2
    sleep 5
  done
  return 1
}

cd "$ATLAS_ROOT"
"$PYTHON" -m cmip6_pipeline.impact plan-gwl \
  --run-root "$RUN_ROOT" --gwl-root "$GWL_ROOT" --geometry-asset "$GEOMETRY" \
  --historical-pair-root "$ATLAS_ROOT/.cmip6-runs/mri-paired" \
  --historical-pair-root "$ATLAS_ROOT/.cmip6-runs/mpi-esm1-2-hr-production" \
  --historical-pair-root "$ATLAS_ROOT/.cmip6-runs/hadgem-ll-paired" \
  --historical-pair-root "$ATLAS_ROOT/.cmip6-runs/mpi-lr-paired" \
  --historical-pair-root "$ATLAS_ROOT/.cmip6-runs/miroc6-paired"

RUN_COUNT="$(wc -l < "$RUN_ROOT/run.tsv")"
PAIR_COUNT="$(wc -l < "$RUN_ROOT/pair.tsv")"
RUN_ID=""
PAIR_ID=""
if (( RUN_COUNT > 0 )); then
  RUN_ID="$(submit_with_retry --array="1-$RUN_COUNT%$RUN_COUNT" \
    scripts/cmip6_impact_run.slurm "$RUN_ROOT/run.tsv")"
  printf '%s\n' "$RUN_ID" > "$RUN_ROOT/run.job-id"
fi
if (( PAIR_COUNT > 0 )); then
  DEPENDENCY=()
  if [[ -n "$RUN_ID" ]]; then
    DEPENDENCY=(--dependency="afterok:$RUN_ID")
  fi
  PAIR_ID="$(submit_with_retry "${DEPENDENCY[@]}" --array="1-$PAIR_COUNT%$PAIR_COUNT" \
    scripts/cmip6_impact_pair.slurm "$RUN_ROOT/pair.tsv")"
  printf '%s\n' "$PAIR_ID" > "$RUN_ROOT/pair.job-id"
fi
printf 'GWL precipitation impacts: %s run tasks%s; %s pair tasks%s.\n' \
  "$RUN_COUNT" "${RUN_ID:+ ($RUN_ID)}" "$PAIR_COUNT" "${PAIR_ID:+ ($PAIR_ID)}"
