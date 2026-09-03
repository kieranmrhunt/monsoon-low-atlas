#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
DATA_ROOT="${LPS_JRA55_ROOT:-$ATLAS_ROOT/data/reanalyses/jra55}"
OUTPUT_ROOT="$DATA_ROOT/tracking"
RUN_ROOT="$ATLAS_ROOT/.reanalysis-runs/jra55-full-195801-202401"
LINK_RUN_ROOT="$RUN_ROOT/parallel-link"
CONCURRENCY="${LPS_JRA55_MONTH_CONCURRENCY:-12}"
LINK_CONCURRENCY="${LPS_REANALYSIS_LINK_CONCURRENCY:-24}"
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
if [[ -s "$RUN_ROOT/parallel-prepare.job-id" ]]; then
	PREPARE_ID="$(sed -n '1p' "$RUN_ROOT/parallel-prepare.job-id")"
else
	PREPARE_DEPENDENCY=()
	CANDIDATE_COUNT="$(find "$OUTPUT_ROOT/candidates" -maxdepth 1 -type f -name 'candidates-*.csv' 2>/dev/null | wc -l)"
	if [[ "$CANDIDATE_COUNT" -lt "$COUNT" ]]; then PREPARE_DEPENDENCY=(--dependency="afterok:$DETECT_ID"); fi
	PREPARE_ID="$(sbatch --parsable "${PREPARE_DEPENDENCY[@]}" \
	  scripts/prepare_parallel_reanalysis_link.slurm jra55 "$OUTPUT_ROOT" "$LINK_RUN_ROOT")"
	printf '%s\n' "$PREPARE_ID" > "$RUN_ROOT/parallel-prepare.job-id"
fi
if [[ -s "$RUN_ROOT/parallel-years.job-id" ]]; then
	LINK_ID="$(sed -n '1p' "$RUN_ROOT/parallel-years.job-id")"
else
	LINK_ID="$(sbatch --parsable --dependency="afterok:$PREPARE_ID" \
	  --array="0-66%$LINK_CONCURRENCY" scripts/link_reanalysis_year.slurm "$LINK_RUN_ROOT")"
	printf '%s\n' "$LINK_ID" > "$RUN_ROOT/parallel-years.job-id"
fi
if [[ -s "$RUN_ROOT/parallel-finalizer.job-id" ]]; then
	FINAL_ID="$(sed -n '1p' "$RUN_ROOT/parallel-finalizer.job-id")"
else
	FINAL_ID="$(sbatch --parsable --dependency="afterok:$LINK_ID" \
	  scripts/finalize_parallel_reanalysis.slurm jra55 "$DATA_ROOT" "$OUTPUT_ROOT" "$LINK_RUN_ROOT")"
	printf '%s\n' "$FINAL_ID" > "$RUN_ROOT/parallel-finalizer.job-id"
fi
echo "JRA-55 1958-01 through 2024-01: standardise $STANDARD_ID, detect $DETECT_ID, link years $LINK_ID, finalizer $FINAL_ID"
