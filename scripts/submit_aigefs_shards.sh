#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
CYCLES="${1:-recent}"
FORCE="${2:-false}"

if [[ "$FORCE" != "true" ]]; then
  while IFS= read -r job_name; do
    case "$job_name" in
      mla-aigefs-mem|mla-aigefs-final)
        echo "An AIGEFS member-parallel update is already queued or running; no duplicate submitted."
        exit 0
        ;;
    esac
  done < <(timeout 30 squeue -h -u "$USER" -o '%j')
fi

if [[ "$CYCLES" == "recent" ]]; then
  CYCLES="$(cd "$ATLAS_ROOT" && "$PYTHON" -m forecast_pipeline.aigefs_shards plan --manifest "$TARGET/manifest.json")"
fi
if [[ -z "$CYCLES" ]]; then
  echo "The rolling 72-hour AIGEFS cycle window is complete."
  exit 0
fi
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/aigefs-shards-$RUN_ID"
JOBS="$RUN_ROOT/jobs.tsv"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
INDEX=0
IFS=',' read -ra CYCLE_VALUES <<< "$CYCLES"
for CYCLE in "${CYCLE_VALUES[@]}"; do
  [[ "$CYCLE" =~ ^[0-9]{10}$ ]]
  for NUMBER in $(seq -w 1 31); do
    INDEX=$((INDEX + 1))
    printf '%d\t%s\tp%s\n' "$INDEX" "$CYCLE" "$NUMBER" >> "$JOBS"
  done
done
cd "$ATLAS_ROOT"
CONCURRENCY="${LPS_AIGEFS_CONCURRENCY:-256}"
PASSES="${LPS_AIGEFS_PASSES:-3}"
[[ "$PASSES" =~ ^[1-5]$ ]] || { echo "LPS_AIGEFS_PASSES must be an integer from 1 to 5" >&2; exit 2; }
ARRAY_IDS=()
PREVIOUS=""
for PASS in $(seq 1 "$PASSES"); do
  DEPENDENCY=()
  if [[ -n "$PREVIOUS" ]]; then
    DEPENDENCY=(--dependency="afterany:$PREVIOUS")
  fi
  ARRAY_ID="$(sbatch --parsable "${DEPENDENCY[@]}" --array="1-$INDEX%$CONCURRENCY" scripts/aigefs_member_shard.slurm "$JOBS" "$RUN_ROOT")"
  ARRAY_IDS+=("$ARRAY_ID")
  PREVIOUS="$ARRAY_ID"
done
FINAL_ID="$(sbatch --parsable --dependency="afterany:$PREVIOUS" scripts/finalize_aigefs_shards.slurm "$RUN_ROOT" "$CYCLES" "$TARGET")"
printf 'AIGEFS member arrays %s (%d shards, %d passes); finalizer %s\n' "${ARRAY_IDS[*]}" "$INDEX" "$PASSES" "$FINAL_ID"
