#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ATLAS_ROOT/hpc-logs"

if [[ -n "$(squeue -h -u "$USER" -n mla-forecast -o '%A')" ]]; then
  echo "An mla-forecast update is already queued or running; no duplicate submitted."
  exit 0
fi

cd "$ATLAS_ROOT"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/$RUN_ID"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
mkdir -p "$RUN_ROOT"

MODELS=(gfs gefs mogreps-g ifs ifs-ens aifs aifs-ens)
JOB_IDS=()
ECMWF_PREVIOUS=""
for model in "${MODELS[@]}"; do
  safe_name="${model//-/_}"
  if [[ "$model" == ifs* || "$model" == aifs* ]]; then
    if [[ -n "$ECMWF_PREVIOUS" ]]; then
      job_id="$(sbatch --parsable --dependency="afterany:$ECMWF_PREVIOUS" --job-name="mla-fc-$safe_name" scripts/update_forecasts.slurm "$model" "$RUN_ROOT/$model")"
    else
      job_id="$(sbatch --parsable --job-name="mla-fc-$safe_name" scripts/update_forecasts.slurm "$model" "$RUN_ROOT/$model")"
    fi
    ECMWF_PREVIOUS="$job_id"
  else
    job_id="$(sbatch --parsable --job-name="mla-fc-$safe_name" scripts/update_forecasts.slurm "$model" "$RUN_ROOT/$model")"
  fi
  JOB_IDS+=("$job_id")
done
DEPENDENCY="$(IFS=:; echo "${JOB_IDS[*]}")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$DEPENDENCY" scripts/finalize_forecasts.slurm "$RUN_ROOT" "$OUTPUT")"
echo "Submitted model jobs ${JOB_IDS[*]} and finalizer $FINAL_ID"
