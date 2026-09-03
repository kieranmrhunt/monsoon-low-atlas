#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=high
#SBATCH --cpus-per-task=8
#SBATCH --mem=12G
#SBATCH --time=04:00:00
#SBATCH --job-name=mla-tigge-harvest
#SBATCH --output=hpc-logs/mla-tigge-harvest-%j.out
#SBATCH --error=hpc-logs/mla-tigge-harvest-%j.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
RUN_ROOT="${1:?TIGGE staging root is required}"
PLAN="${2:?TIGGE plan is required}"
PUBLIC_ROOT="${3:?public forecast root is required}"
CREATED_AFTER="${4:?inclusive ECDS creation timestamp is required}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
MODELS="${LPS_TIGGE_RECOVERY_MODELS:-tigge-imd,tigge-ncmrwf}"

cd "$ATLAS_ROOT"
"$PYTHON" -m forecast_pipeline.recover_tigge_jobs \
  --created-after "$CREATED_AFTER" \
  --resume-inventory \
  --run-root "$RUN_ROOT" \
  --public-root "$PUBLIC_ROOT" \
  --models "$MODELS" \
  --workers 16 \
  --download \
  --download-workers 8

JOBS="$RUN_ROOT/recovered-jobs.tsv"
COUNT="$(wc -l < "$JOBS")"
if [[ "$COUNT" == "0" ]]; then
  echo "No completed unpublished ECDS TIGGE jobs need local processing."
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT="$RUN_ROOT/recovered-jobs-$STAMP.tsv"
cp "$JOBS" "$SNAPSHOT"
PROCESS_ID="$(/usr/bin/timeout 60s /usr/bin/sbatch --parsable --qos=high --cpus-per-task=8 --mem=24G --time=05:30:00 \
  --array="1-$COUNT%12" --job-name=mla-tigge-recover \
  --output=hpc-logs/mla-tigge-recover-%A_%a.out \
  --error=hpc-logs/mla-tigge-recover-%A_%a.err \
  scripts/backfill_forecast_cycle.slurm "$SNAPSHOT" "$RUN_ROOT" tigge "$PUBLIC_ROOT")"
FINAL_ID="$(/usr/bin/timeout 60s /usr/bin/sbatch --parsable --dependency="afterany:$PROCESS_ID" \
  scripts/finalize_forecast_archive_backfill.slurm "$RUN_ROOT" "$PLAN" "$PUBLIC_ROOT" tigge)"
echo "Submitted TIGGE recovery array $PROCESS_ID for $COUNT cycle(s) and finalizer $FINAL_ID."
