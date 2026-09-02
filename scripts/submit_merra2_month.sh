#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MONTH_DASHED="${1:?month YYYY-MM is required}"
MONTH_COMPACT="${MONTH_DASHED//-/}"
DATA_ROOT="${LPS_MERRA2_ROOT:-$ATLAS_ROOT/data/reanalyses/merra2}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
RUN_ROOT="$ATLAS_ROOT/.reanalysis-runs/merra2-$MONTH_COMPACT"
JOBS="$RUN_ROOT/jobs.tsv"
LEDGER="$DATA_ROOT/opendap-ledger.json"
mkdir -p "$RUN_ROOT" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
"$PYTHON" -m reanalysis_pipeline.plan_merra2 --month "$MONTH_DASHED" --jobs "$JOBS"
COUNT="$(wc -l < "$JOBS")"
ARRAY_CONCURRENCY="${LPS_MERRA2_ARRAY_CONCURRENCY:-16}"
DOWNLOAD_ID="$(sbatch --parsable --array="1-$COUNT%$ARRAY_CONCURRENCY" scripts/download_merra2_day.slurm "$JOBS" "$DATA_ROOT" "$LEDGER")"
STANDARD_ID="$(sbatch --parsable --dependency="afterok:$DOWNLOAD_ID" scripts/standardise_merra2_month.slurm "$DATA_ROOT" "$MONTH_COMPACT")"
echo "Submitted MERRA-2 daily array $DOWNLOAD_ID ($COUNT tasks) and standardizer $STANDARD_ID"
