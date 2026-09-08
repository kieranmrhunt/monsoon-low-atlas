#!/bin/bash
# Repair a selected raw-input month, then resume an existing full-period plan.
set -euo pipefail
ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FULL_RUN="${1:?existing full-period run directory is required}"
REPAIR_MONTH="${2:?raw-input month YYYY-MM is required}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
DATA_ROOT="${LPS_MERRA2_ROOT:-$ATLAS_ROOT/data/reanalyses/merra2}"
OUTPUT_ROOT="$DATA_ROOT/tracking"
LEDGER="$DATA_ROOT/opendap-ledger.json"
LINK_ROOT="$FULL_RUN/parallel-link"
cd "$ATLAS_ROOT"
[[ -s "$FULL_RUN/months.tsv" && -s "$FULL_RUN/days.tsv" ]]
FULL_RUN="$(cd "$FULL_RUN" && pwd)"
LINK_ROOT="$FULL_RUN/parallel-link"

# Do not start a second writer for an already running chain.
ACTIVE_JOB_IDS="$(squeue -h -u "$USER" -o '%i')"
for marker in reconcile standardise detect parallel-prepare parallel-years parallel-finalizer; do
  if [[ -s "$FULL_RUN/$marker.job-id" ]]; then
    old_id="$(head -1 "$FULL_RUN/$marker.job-id")"
    while IFS= read -r active_id; do
      case "$active_id" in
        "$old_id"|"${old_id}_"*)
          echo "Existing $marker job $old_id is still active; inspect it before resubmitting." >&2
          exit 1
          ;;
      esac
    done <<< "$ACTIVE_JOB_IDS"
  fi
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPAIR_ROOT="$FULL_RUN/input-repairs/$STAMP"
mkdir -p "$REPAIR_ROOT/previous-job-ids" "$ATLAS_ROOT/hpc-logs"
for marker in reconcile standardise detect parallel-prepare parallel-years parallel-finalizer; do
  if [[ -f "$FULL_RUN/$marker.job-id" ]]; then
    cp -p "$FULL_RUN/$marker.job-id" "$REPAIR_ROOT/previous-job-ids/"
  fi
done
"$PYTHON" -m reanalysis_pipeline.plan_merra2_backfill \
  --start "$REPAIR_MONTH" --end "$REPAIR_MONTH" --output "$REPAIR_ROOT"
DAY_COUNT="$(wc -l < "$REPAIR_ROOT/days.tsv")"
MONTH_COUNT="$(wc -l < "$FULL_RUN/months.tsv")"
FIRST_MONTH="$(head -1 "$FULL_RUN/months.tsv" | cut -f2)"
LAST_MONTH="$(tail -1 "$FULL_RUN/months.tsv" | cut -f2)"
YEAR_COUNT="$((10#${LAST_MONTH:0:4} - 10#${FIRST_MONTH:0:4} + 1))"

record() {
  printf '%s\t%s\n' "$1" "$2" >> "$REPAIR_ROOT/submitted-jobs.tsv"
  printf '%s\n' "$2" > "$FULL_RUN/$1.job-id"
}
DOWNLOAD_ID="$(sbatch --parsable --array="1-$DAY_COUNT%${LPS_MERRA2_DOWNLOAD_CONCURRENCY:-12}" \
  scripts/download_merra2_day.slurm "$REPAIR_ROOT/days.tsv" "$DATA_ROOT" "$LEDGER")"
printf 'repair-download\t%s\n' "$DOWNLOAD_ID" >> "$REPAIR_ROOT/submitted-jobs.tsv"

# The full validated ledger is the gate, not the existence of an old job ID.
# Cancel descendants if a parent fails instead of leaving immortal dependency jobs.
LEDGER_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$DOWNLOAD_ID" \
  scripts/reconcile_merra2_range.slurm "$FULL_RUN/days.tsv" "$DATA_ROOT" "$LEDGER")"
record reconcile "$LEDGER_ID"
STANDARD_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$LEDGER_ID" \
  --array="1-$MONTH_COUNT%${LPS_MERRA2_MONTH_CONCURRENCY:-72}" \
  scripts/standardise_merra2_month.slurm "$FULL_RUN/months.tsv" "$DATA_ROOT")"
record standardise "$STANDARD_ID"
DETECT_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$STANDARD_ID" \
  --array="1-$MONTH_COUNT%${LPS_MERRA2_MONTH_CONCURRENCY:-72}" \
  scripts/detect_reanalysis_month.slurm merra2 "$FULL_RUN/months.tsv" "$DATA_ROOT" "$OUTPUT_ROOT")"
record detect "$DETECT_ID"
PREPARE_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$DETECT_ID" \
  scripts/prepare_parallel_reanalysis_link.slurm merra2 "$OUTPUT_ROOT" "$LINK_ROOT")"
record parallel-prepare "$PREPARE_ID"
LINK_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$PREPARE_ID" \
  --array="0-$((YEAR_COUNT-1))%${LPS_REANALYSIS_LINK_CONCURRENCY:-24}" \
  scripts/link_reanalysis_year.slurm "$LINK_ROOT")"
record parallel-years "$LINK_ID"
FINAL_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$LINK_ID" \
  scripts/finalize_parallel_reanalysis.slurm merra2 "$DATA_ROOT" "$OUTPUT_ROOT" "$LINK_ROOT")"
record parallel-finalizer "$FINAL_ID"
printf 'Repair download %s; ledger %s; standardise %s; detect %s; link %s; finalizer %s\n' \
  "$DOWNLOAD_ID" "$LEDGER_ID" "$STANDARD_ID" "$DETECT_ID" "$LINK_ID" "$FINAL_ID"
printf 'Repair audit: %s\n' "$REPAIR_ROOT"
