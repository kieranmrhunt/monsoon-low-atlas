#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ATLAS_ROOT/hpc-logs"

# Only suppress a submission when an operational refresh is already active.
# Archive/backfill finalizers historically used the generic `mla-forecast`
# name too, so matching that name caused unrelated archive work to skip a
# six-hourly live update.
while IFS= read -r job_name; do
  case "$job_name" in
    mla-fc-gfs|mla-fc-gefs|mla-fc-aigfs|mla-fc-aigefs|\
    mla-fc-graphcast_noaa|mla-fc-graphcast_ifs_noaa|mla-fc-mogreps_g|\
    mla-fc-ifs|mla-fc-ifs_ens|mla-fc-aifs|mla-fc-aifs_ens|\
    mla-fc-operational)
      echo "An operational forecast update is already queued or running; no duplicate submitted."
      /usr/bin/bash "$ATLAS_ROOT/scripts/submit_forecast_recent_backfill.sh"
      exit 0
      ;;
  esac
done < <(timeout 30 squeue -h -u "$USER" -o '%j')

cd "$ATLAS_ROOT"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/$RUN_ID"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
mkdir -p "$RUN_ROOT"

MODELS=(gfs gefs aigfs aigefs graphcast-noaa graphcast-ifs-noaa mogreps-g ifs ifs-ens aifs aifs-ens)
JOB_IDS=()
ECMWF_PREVIOUS=""
for model in "${MODELS[@]}"; do
  safe_name="${model//-/_}"
  if [[ "$model" == ifs* || "$model" == aifs* ]]; then
    if [[ -n "$ECMWF_PREVIOUS" ]]; then
      job_id="$(sbatch --parsable --dependency="afterany:$ECMWF_PREVIOUS" --job-name="mla-fc-$safe_name" scripts/update_forecasts.slurm "$model" "$RUN_ROOT/$model" "$OUTPUT")"
    else
      job_id="$(sbatch --parsable --job-name="mla-fc-$safe_name" scripts/update_forecasts.slurm "$model" "$RUN_ROOT/$model" "$OUTPUT")"
    fi
    ECMWF_PREVIOUS="$job_id"
  else
    job_id="$(sbatch --parsable --job-name="mla-fc-$safe_name" scripts/update_forecasts.slurm "$model" "$RUN_ROOT/$model" "$OUTPUT")"
  fi
  JOB_IDS+=("$job_id")
done
DEPENDENCY="$(IFS=:; echo "${JOB_IDS[*]}")"
FINAL_ID="$(sbatch --parsable --job-name=mla-fc-operational \
  --dependency="afterany:$DEPENDENCY" \
  scripts/finalize_forecasts.slurm "$RUN_ROOT" "$OUTPUT")"
echo "Submitted model jobs ${JOB_IDS[*]} and finalizer $FINAL_ID"
/usr/bin/bash "$ATLAS_ROOT/scripts/submit_forecast_recent_backfill.sh"
