#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_CMIP6_HIGHRES_IMPACT_ROOT:-$ATLAS_ROOT/.cmip6-runs/highres-impact-production}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
GEOMETRY="$ATLAS_ROOT/assets/atlas-core.cefb51e2bde1.json.gz"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"

if [[ -s "$RUN_ROOT/run.job-id" && "${LPS_CMIP6_IMPACT_FORCE_SUBMIT:-0}" != "1" ]]; then
  printf 'A HighResMIP impact submission is already recorded in %s; set LPS_CMIP6_IMPACT_FORCE_SUBMIT=1 for a resumable retry.\n' "$RUN_ROOT"
  exit 0
fi

cd "$ATLAS_ROOT"
"$PYTHON" -m cmip6_pipeline.impact plan --run-root "$RUN_ROOT" --geometry-asset "$GEOMETRY" \
  --pair-root "$ATLAS_ROOT/.cmip6-runs/highres-cnrm-paired" \
  --pair-root "$ATLAS_ROOT/.cmip6-runs/highres-ecearth-paired" \
  --pair-root "$ATLAS_ROOT/.cmip6-runs/highres-ecearth-hr-paired"
RUN_COUNT="$(wc -l < "$RUN_ROOT/run.tsv")"
PAIR_COUNT="$(wc -l < "$RUN_ROOT/pair.tsv")"
RUN_ID="$(sbatch --parsable --array="1-$RUN_COUNT%$RUN_COUNT" scripts/cmip6_impact_run.slurm "$RUN_ROOT/run.tsv")"
PAIR_ID="$(sbatch --parsable --dependency="afterok:$RUN_ID" --array="1-$PAIR_COUNT%$PAIR_COUNT" scripts/cmip6_impact_pair.slurm "$RUN_ROOT/pair.tsv")"
printf '%s\n' "$RUN_ID" > "$RUN_ROOT/run.job-id"
printf '%s\n' "$PAIR_ID" > "$RUN_ROOT/pair.job-id"
printf 'HighResMIP precipitation impacts: runs %s, pairs %s\n' "$RUN_ID" "$PAIR_ID"
