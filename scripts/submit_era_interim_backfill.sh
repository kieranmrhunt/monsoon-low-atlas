#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
DATA_ROOT="${LPS_ERA_INTERIM_ROOT:-$ATLAS_ROOT/data/reanalyses/erainterim}"
OUTPUT_ROOT="$DATA_ROOT/tracking"
RUN_ROOT="$ATLAS_ROOT/.reanalysis-runs/erainterim-full-197901-201908"
CONCURRENCY="${LPS_ERA_INTERIM_MONTH_CONCURRENCY:-48}"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m reanalysis_pipeline.plan_months \
  --start 1979-01 --end 2019-08 --output "$RUN_ROOT/months.tsv"
COUNT="$(wc -l < "$RUN_ROOT/months.tsv")"
if [[ -s "$RUN_ROOT/standardise.job-id" ]]; then
	STANDARD_ID="$(sed -n '1p' "$RUN_ROOT/standardise.job-id")"
else
	STANDARD_ID="$(sbatch --parsable --array="1-$COUNT%$CONCURRENCY" \
	  scripts/standardise_era_interim_month.slurm "$RUN_ROOT/months.tsv" "$DATA_ROOT")"
	printf '%s\n' "$STANDARD_ID" > "$RUN_ROOT/standardise.job-id"
fi
if [[ -s "$RUN_ROOT/detect.job-id" ]]; then
	DETECT_ID="$(sed -n '1p' "$RUN_ROOT/detect.job-id")"
else
	DETECT_ID="$(sbatch --parsable --dependency="afterok:$STANDARD_ID" --array="1-$COUNT%$CONCURRENCY" \
	  scripts/detect_reanalysis_month.slurm erainterim "$RUN_ROOT/months.tsv" "$DATA_ROOT" "$OUTPUT_ROOT")"
	printf '%s\n' "$DETECT_ID" > "$RUN_ROOT/detect.job-id"
fi
if [[ -s "$RUN_ROOT/finalizer.job-id" ]]; then
	FINAL_ID="$(sed -n '1p' "$RUN_ROOT/finalizer.job-id")"
else
	FINAL_ID="$(sbatch --parsable --dependency="afterok:$DETECT_ID" \
	  scripts/finalize_reanalysis.slurm erainterim "$DATA_ROOT" "$OUTPUT_ROOT")"
	printf '%s\n' "$FINAL_ID" > "$RUN_ROOT/finalizer.job-id"
fi
echo "ERA-Interim 1979-01 through 2019-08: standardise $STANDARD_ID, detect $DETECT_ID, finalizer $FINAL_ID"
