#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
OUTPUT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
SOURCE_PLAN="${LPS_CMA_TIGGE_SOURCE_PLAN:-$ATLAS_ROOT/.forecast-runs/tigge-other-centres-fast-20260831T091601Z/plan.json}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$ATLAS_ROOT/.forecast-runs/cma-tigge-recovery-$RUN_ID"
PLAN="$RUN_ROOT/plan.json"
STATE="$RUN_ROOT/state.json"

if [[ ! -f "$SOURCE_PLAN" ]]; then
  echo "Missing CMA recovery source plan: $SOURCE_PLAN" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"
cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.plan_cma_tigge_recovery \
  --manifest "$OUTPUT/manifest.json" \
  --plans "$SOURCE_PLAN" \
  --output "$PLAN"

echo "CMA recovery plan: $PLAN"
echo "Submission state: $STATE"
if [[ "${LPS_CMA_TIGGE_SUBMIT:-0}" == "1" ]]; then
  "$PYTHON" -m forecast_pipeline.cma_tigge submit-plan \
    --plan "$PLAN" \
    --state "$STATE" \
    --limit "${LPS_CMA_TIGGE_SUBMIT_LIMIT:-4}"
else
  echo "Plan only. Set CMA credentials and LPS_CMA_TIGGE_SUBMIT=1 to submit bounded applications."
fi
