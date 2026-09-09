#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
CYCLES="${1:-recent}"
FORCE="${2:-false}"

mkdir -p "$ATLAS_ROOT/.forecast-runs"
exec 9>"$ATLAS_ROOT/.forecast-runs/weathernext2-submit.lock"
if ! /usr/bin/flock -n 9; then
  echo "Another WeatherNext 2 submission planner is active; no duplicate submitted."
  exit 0
fi

ADC_PATH="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}"
if [[ ! -r "$ADC_PATH" ]]; then
  echo "WeatherNext 2 skipped: Google Application Default Credentials are not configured at $ADC_PATH."
  exit 0
fi

if [[ "$FORCE" != "true" ]]; then
  if ! QUEUED_JOBS="$(timeout 30 squeue -h -u "$USER" -o '%j')"; then
    echo "Could not inspect the Slurm queue; no WeatherNext 2 duplicate-risk submission made."
    exit 0
  fi
  while IFS= read -r job_name; do
    case "$job_name" in
      mla-wn2-member|mla-wn2-final)
        echo "A WeatherNext 2 member-parallel update is already queued or running; no duplicate submitted."
        exit 0
        ;;
    esac
  done <<< "$QUEUED_JOBS"
fi

if [[ "$CYCLES" == "recent" ]]; then
  CYCLES="$(cd "$ATLAS_ROOT" && "$PYTHON" -m forecast_pipeline.weathernext2_shards plan --manifest "$TARGET/manifest.json")"
fi
if [[ -z "$CYCLES" ]]; then
  echo "The rolling 72-hour WeatherNext 2 cycle window is complete."
  exit 0
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/weathernext2-shards-$RUN_ID"
JOBS="$RUN_ROOT/jobs.tsv"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
INDEX=0
IFS=',' read -ra CYCLE_VALUES <<< "$CYCLES"
for CYCLE in "${CYCLE_VALUES[@]}"; do
  [[ "$CYCLE" =~ ^[0-9]{10}$ ]]
  for NUMBER in $(seq -w 0 63); do
    INDEX=$((INDEX + 1))
    printf '%d\t%s\tm%s\n' "$INDEX" "$CYCLE" "$NUMBER" >> "$JOBS"
  done
done

cd "$ATLAS_ROOT"
CONCURRENCY="${LPS_WEATHERNEXT_CONCURRENCY:-64}"
PASSES="${LPS_WEATHERNEXT_PASSES:-2}"
[[ "$PASSES" =~ ^[1-4]$ ]] || {
  echo "LPS_WEATHERNEXT_PASSES must be an integer from 1 to 4" >&2
  exit 2
}
ARRAY_IDS=()
PREVIOUS=""
for PASS in $(seq 1 "$PASSES"); do
  DEPENDENCY=()
  if [[ -n "$PREVIOUS" ]]; then
    DEPENDENCY=(--dependency="afterany:$PREVIOUS")
  fi
  ARRAY_ID="$(sbatch --parsable "${DEPENDENCY[@]}" --array="1-$INDEX%$CONCURRENCY" scripts/weathernext2_member_shard.slurm "$JOBS" "$RUN_ROOT")"
  ARRAY_IDS+=("$ARRAY_ID")
  PREVIOUS="$ARRAY_ID"
done
FINAL_ID="$(sbatch --parsable --dependency="afterany:$PREVIOUS" scripts/finalize_weathernext2_shards.slurm "$RUN_ROOT" "$CYCLES" "$TARGET")"
printf 'WeatherNext 2 member arrays %s (%d shards, %d passes); finalizer %s\n' "${ARRAY_IDS[*]}" "$INDEX" "$PASSES" "$FINAL_ID"
