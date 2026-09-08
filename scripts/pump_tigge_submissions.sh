#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_TIGGE_SUBMIT_RUN_ROOT:-$ATLAS_ROOT/.forecast-runs/tigge-india-priority-20260831T220116Z}"
PLAN="${LPS_TIGGE_SUBMIT_PLAN:-$RUN_ROOT/plan.json}"
PUBLIC_ROOT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"

cd "$ATLAS_ROOT"
if /usr/bin/timeout 15s /usr/bin/squeue -h -u kieran -n mla-tigge-submit \
  | /usr/bin/head -1 | /usr/bin/grep -q .; then
  echo "A non-blocking TIGGE submission pump is already active."
  exit 0
fi

JOB_ID="$(/usr/bin/timeout 30s /usr/bin/sbatch --parsable scripts/submit_tigge_requests.slurm \
  "$RUN_ROOT" "$PLAN" "$PUBLIC_ROOT")"
echo "Submitted non-blocking TIGGE request pump $JOB_ID."
