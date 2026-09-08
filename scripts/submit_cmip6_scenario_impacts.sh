#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_CMIP6_SCENARIO_IMPACT_ROOT:-$ATLAS_ROOT/.cmip6-runs/additional-scenario-impact-production}"
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
"$PYTHON" -m cmip6_pipeline.impact plan --run-root "$RUN_ROOT" --geometry-asset "$GEOMETRY" \
  --pair-root "$ATLAS_ROOT/.cmip6-runs/hadgem-mm-ssp126-paired" \
  --pair-root "$ATLAS_ROOT/.cmip6-runs/hadgem-mm-ssp585-paired"
RUN_COUNT="$(wc -l < "$RUN_ROOT/run.tsv")"
PAIR_COUNT="$(wc -l < "$RUN_ROOT/pair.tsv")"
if [[ -s "$RUN_ROOT/run.job-id" && "${LPS_CMIP6_IMPACT_FORCE_SUBMIT:-0}" != "1" ]]; then
  RUN_ID="$(<"$RUN_ROOT/run.job-id")"
else
  RUN_ID="$(submit_with_retry --array="1-$RUN_COUNT%$RUN_COUNT" scripts/cmip6_impact_run.slurm "$RUN_ROOT/run.tsv")"
  printf '%s\n' "$RUN_ID" > "$RUN_ROOT/run.job-id"
fi
if [[ -s "$RUN_ROOT/pair.job-id" && "${LPS_CMIP6_IMPACT_FORCE_SUBMIT:-0}" != "1" ]]; then
  PAIR_ID="$(<"$RUN_ROOT/pair.job-id")"
else
  PAIR_ID="$(submit_with_retry --dependency="afterok:$RUN_ID" --array="1-$PAIR_COUNT%$PAIR_COUNT" scripts/cmip6_impact_pair.slurm "$RUN_ROOT/pair.tsv")"
  printf '%s\n' "$PAIR_ID" > "$RUN_ROOT/pair.job-id"
fi
printf 'Additional SSP precipitation impacts: runs %s, pairs %s\n' "$RUN_ID" "$PAIR_ID"
