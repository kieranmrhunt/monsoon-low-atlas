#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$("$PYTHON" -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ATLAS_ROOT/.forecast-runs" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"

submit_plan() {
  local mode="$1"
  local run_root="$ATLAS_ROOT/.forecast-runs/${mode}-backfill-$RUN_ID"
  local plan="$run_root/plan.json"
  local jobs="$run_root/jobs.tsv"
  mkdir -p "$run_root"
  if [[ "$mode" == "recent" ]]; then
    "$PYTHON" -m forecast_pipeline.plan_recent --manifest "$OUTPUT/manifest.json" --output "$plan" --jobs "$jobs"
  else
    "$PYTHON" -m forecast_pipeline.plan_archive --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME" --manifest "$OUTPUT/manifest.json" --output "$plan" --jobs "$jobs"
  fi
  local count
  count="$(wc -l < "$jobs")"
  if [[ "$count" == 0 ]]; then
    echo "$mode backfill is already complete."
    return
  fi
  local array_id
  array_id="$(sbatch --parsable --array="1-$count" scripts/backfill_forecast_cycle.slurm "$jobs" "$run_root" "$mode")"
  local final_id
  if [[ "$mode" == "recent" ]]; then
    final_id="$(sbatch --parsable --dependency="afterany:$array_id" scripts/finalize_forecast_recent_backfill.slurm "$run_root" "$plan" "$OUTPUT")"
  else
    final_id="$(sbatch --parsable --dependency="afterany:$array_id" scripts/finalize_forecast_archive_backfill.slurm "$run_root" "$plan" "$OUTPUT")"
  fi
  echo "Submitted $mode array $array_id ($count tasks) and finalizer $final_id"
}

submit_plan recent
submit_plan archive
