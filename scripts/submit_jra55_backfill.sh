#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
DATA_ROOT="${LPS_JRA55_ROOT:-$ATLAS_ROOT/data/reanalyses/jra55}"
OUTPUT_ROOT="$DATA_ROOT/tracking"
RUN_ROOT="$ATLAS_ROOT/.reanalysis-runs/jra55-full-195801-202401"
CONCURRENCY="${LPS_JRA55_MONTH_CONCURRENCY:-12}"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m reanalysis_pipeline.plan_months \
  --start 1958-01 --end 2024-01 --output "$RUN_ROOT/months.tsv"
COUNT="$(wc -l < "$RUN_ROOT/months.tsv")"
if [[ -s "$RUN_ROOT/standardise.job-id" ]]; then
	STANDARD_ID="$(sed -n '1p' "$RUN_ROOT/standardise.job-id")"
else
	STANDARD_ID="$(sbatch --parsable --array="1-$COUNT%$CONCURRENCY" \
	  scripts/standardise_jra55_month.slurm "$RUN_ROOT/months.tsv" "$DATA_ROOT")"
	printf '%s\n' "$STANDARD_ID" > "$RUN_ROOT/standardise.job-id"
fi
if [[ -s "$RUN_ROOT/detect.job-id" ]]; then
	DETECT_ID="$(sed -n '1p' "$RUN_ROOT/detect.job-id")"
else
	DETECT_ID="$(sbatch --parsable --dependency="afterok:$STANDARD_ID" --array="1-$COUNT%$CONCURRENCY" \
	  scripts/detect_reanalysis_month.slurm jra55 "$RUN_ROOT/months.tsv" "$DATA_ROOT" "$OUTPUT_ROOT")"
	printf '%s\n' "$DETECT_ID" > "$RUN_ROOT/detect.job-id"
fi
if [[ -s "$RUN_ROOT/finalizer.job-id" ]]; then
	FINAL_ID="$(sed -n '1p' "$RUN_ROOT/finalizer.job-id")"
else
	FINAL_ID="$(sbatch --parsable --dependency="afterok:$DETECT_ID" \
	  scripts/finalize_reanalysis.slurm jra55 "$DATA_ROOT" "$OUTPUT_ROOT")"
	printf '%s\n' "$FINAL_ID" > "$RUN_ROOT/finalizer.job-id"
fi
echo "JRA-55 1958-01 through 2024-01: standardise $STANDARD_ID, detect $DETECT_ID, finalizer $FINAL_ID"
