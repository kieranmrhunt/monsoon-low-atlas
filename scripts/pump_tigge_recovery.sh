#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${LPS_TIGGE_RECOVERY_RUN_ROOT:-$ATLAS_ROOT/.forecast-runs/tigge-india-priority-20260831T220116Z}"
PLAN="${LPS_TIGGE_RECOVERY_PLAN:-$RUN_ROOT/plan.json}"
PUBLIC_ROOT="${LPS_FORECAST_OUT:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-forecasts-v1}"
CREATED_AFTER="${LPS_TIGGE_RECOVERY_CREATED_AFTER:-2026-08-31T21:00:00Z}"

cd "$ATLAS_ROOT"
if /usr/bin/timeout 15s /usr/bin/squeue -h -u kieran \
  -n mla-tigge-harvest,mla-tigge-recover \
  | /usr/bin/head -1 | /usr/bin/grep -q .; then
  echo "A TIGGE harvester or local recovery array is already active."
  exit 0
fi

JOB_ID="$(/usr/bin/timeout 30s /usr/bin/sbatch --parsable scripts/harvest_tigge_successes.slurm \
  "$RUN_ROOT" "$PLAN" "$PUBLIC_ROOT" "$CREATED_AFTER")"
echo "Submitted TIGGE remote-result harvester $JOB_ID."
