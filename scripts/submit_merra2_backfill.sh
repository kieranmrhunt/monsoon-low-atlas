#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
START_MONTH="${LPS_MERRA2_START_MONTH:-1980-01}"
END_MONTH="${LPS_MERRA2_END_MONTH:-${1:-}}"
if [[ -z "$END_MONTH" ]]; then
  END_MONTH="$($PYTHON -m reanalysis_pipeline.merra2 latest-complete-month)"
fi
DATA_ROOT="${LPS_MERRA2_ROOT:-$ATLAS_ROOT/data/reanalyses/merra2}"
OUTPUT_ROOT="$DATA_ROOT/tracking"
RUN_ROOT="$ATLAS_ROOT/.reanalysis-runs/merra2-full-$START_MONTH-$END_MONTH"
LEDGER="$DATA_ROOT/opendap-ledger.json"
ARRAY_CONCURRENCY="${LPS_MERRA2_DOWNLOAD_CONCURRENCY:-12}"
MONTH_CONCURRENCY="${LPS_MERRA2_MONTH_CONCURRENCY:-72}"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m reanalysis_pipeline.plan_merra2_backfill \
  --start "$START_MONTH" --end "$END_MONTH" --output "$RUN_ROOT"

PREVIOUS_ID=""
for JOBS in "$RUN_ROOT"/day-chunks/days-*.tsv; do
	ID_FILE="$RUN_ROOT/$(basename "$JOBS" .tsv).job-id"
	if [[ -s "$ID_FILE" ]]; then
		PREVIOUS_ID="$(sed -n '1p' "$ID_FILE")"
		continue
	fi
  COUNT="$(wc -l < "$JOBS")"
  DEPENDENCY=()
  if [[ -n "$PREVIOUS_ID" ]]; then DEPENDENCY=(--dependency="afterok:$PREVIOUS_ID"); fi
  PREVIOUS_ID="$(sbatch --parsable "${DEPENDENCY[@]}" \
    --array="1-$COUNT%$ARRAY_CONCURRENCY" \
    scripts/download_merra2_day.slurm "$JOBS" "$DATA_ROOT" "$LEDGER")"
	printf '%s\n' "$PREVIOUS_ID" > "$ID_FILE"
done
if [[ -s "$RUN_ROOT/reconcile.job-id" ]]; then
	LEDGER_ID="$(sed -n '1p' "$RUN_ROOT/reconcile.job-id")"
else
	LEDGER_ID="$(sbatch --parsable --dependency="afterok:$PREVIOUS_ID" \
	  scripts/reconcile_merra2_range.slurm "$RUN_ROOT/days.tsv" "$DATA_ROOT" "$LEDGER")"
	printf '%s\n' "$LEDGER_ID" > "$RUN_ROOT/reconcile.job-id"
fi
MONTH_COUNT="$(wc -l < "$RUN_ROOT/months.tsv")"
if [[ -s "$RUN_ROOT/standardise.job-id" ]]; then
	STANDARD_ID="$(sed -n '1p' "$RUN_ROOT/standardise.job-id")"
else
	STANDARD_ID="$(sbatch --parsable --dependency="afterok:$LEDGER_ID" \
	  --array="1-$MONTH_COUNT%$MONTH_CONCURRENCY" \
	  scripts/standardise_merra2_month.slurm "$RUN_ROOT/months.tsv" "$DATA_ROOT")"
	printf '%s\n' "$STANDARD_ID" > "$RUN_ROOT/standardise.job-id"
fi
if [[ -s "$RUN_ROOT/detect.job-id" ]]; then
	DETECT_ID="$(sed -n '1p' "$RUN_ROOT/detect.job-id")"
else
	DETECT_ID="$(sbatch --parsable --dependency="afterok:$STANDARD_ID" \
	  --array="1-$MONTH_COUNT%$MONTH_CONCURRENCY" \
	  scripts/detect_reanalysis_month.slurm merra2 "$RUN_ROOT/months.tsv" "$DATA_ROOT" "$OUTPUT_ROOT")"
	printf '%s\n' "$DETECT_ID" > "$RUN_ROOT/detect.job-id"
fi
if [[ -s "$RUN_ROOT/finalizer.job-id" ]]; then
	FINAL_ID="$(sed -n '1p' "$RUN_ROOT/finalizer.job-id")"
else
	FINAL_ID="$(sbatch --parsable --dependency="afterok:$DETECT_ID" \
	  scripts/finalize_reanalysis.slurm merra2 "$DATA_ROOT" "$OUTPUT_ROOT")"
	printf '%s\n' "$FINAL_ID" > "$RUN_ROOT/finalizer.job-id"
fi
echo "MERRA-2 $START_MONTH through $END_MONTH: downloads end at $PREVIOUS_ID, ledger $LEDGER_ID, standardise $STANDARD_ID, detect $DETECT_ID, finalizer $FINAL_ID"
