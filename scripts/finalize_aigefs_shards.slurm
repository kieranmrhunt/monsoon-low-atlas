#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=20G
#SBATCH --time=01:00:00
#SBATCH --job-name=mla-aigefs-final
#SBATCH --output=hpc-logs/mla-aigefs-final-%j.out
#SBATCH --error=hpc-logs/mla-aigefs-final-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?run root is required}"
CYCLES="${2:?comma-separated cycles are required}"
TARGET="${3:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
BUILD_MANIFEST="$ATLAS_ROOT/assets/atlas-build-manifest.json"
CORE_NAME="$("$PYTHON" -c "import json; print(json.load(open('$BUILD_MANIFEST'))['core'])")"
cd "$ATLAS_ROOT"
IFS=',' read -ra CYCLE_VALUES <<< "$CYCLES"
SUCCESS=0
for CYCLE in "${CYCLE_VALUES[@]}"; do
  MEMBER_ROOT="$RUN_ROOT/members/$CYCLE"
  COUNT="$(find "$MEMBER_ROOT" -maxdepth 1 -type f -name 'p??.npz' 2>/dev/null | wc -l)"
  if (( COUNT < 22 )); then
    echo "Skipping AIGEFS $CYCLE: only $COUNT/31 member shards completed" >&2
    continue
  fi
  STAGING="$RUN_ROOT/combined/$CYCLE"
  "$PYTHON" -m forecast_pipeline.aigefs_shards combine \
    --cycle "$CYCLE" \
    --members "$MEMBER_ROOT" \
    --output-root "$STAGING" \
    --atlas-core "$ATLAS_ROOT/assets/$CORE_NAME"
  "$PYTHON" -m forecast_pipeline.merge_runs \
    --target "$TARGET" \
    --run-root "$STAGING" \
    --partial \
    --keep-staging
  SUCCESS=$((SUCCESS + 1))
done
(( SUCCESS > 0 ))
