#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/ai-operational-$RUN_ID"
MODELS=(aigfs aigefs graphcast-noaa)

mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
JOB_IDS=()
for MODEL in "${MODELS[@]}"; do
  SAFE_NAME="${MODEL//-/_}"
  JOB_ID="$(sbatch --parsable --qos=high --cpus-per-task=8 --mem=24G \
    --time=12:00:00 --job-name="mla-fc-$SAFE_NAME" \
    scripts/update_forecasts.slurm "$MODEL" "$RUN_ROOT/$MODEL")"
  JOB_IDS+=("$JOB_ID")
done
DEPENDENCY="$(IFS=:; echo "${JOB_IDS[*]}")"
FINAL_ID="$(sbatch --parsable --dependency="afterany:$DEPENDENCY" \
  scripts/finalize_forecasts.slurm "$RUN_ROOT" "$OUTPUT" partial)"

echo "Submitted operational AI model jobs ${JOB_IDS[*]} and partial publisher $FINAL_ID"
echo "Run root: $RUN_ROOT"
