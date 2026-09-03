#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=112G
#SBATCH --time=24:00:00
#SBATCH --job-name=mla-rean-final
#SBATCH --output=hpc-logs/mla-rean-final-%j.out
#SBATCH --error=hpc-logs/mla-rean-final-%j.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
SOURCE="${1:?source is required}"
DATA_ROOT="${2:?standard data root is required}"
OUTPUT_ROOT="${3:?tracking output root is required}"
RUN_ROOT="${4:?parallel-link run root is required}"
PUBLIC_ROOT="${5:-/home/users/kieran/incompass/public/kieran/track_data/LPS/atlas-reanalyses-v1}"
ERA5="${6:-$ATLAS_ROOT/../lps-v5.3-continuity-framework/production/v5.6/public-release/lps_v5.6-era5-1940-2025-core.parquet}"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ASSET="$DATA_ROOT/assets/$SOURCE-matches.json.gz"
NATIVE="$DATA_ROOT/assets/native/$SOURCE"
cd "$ATLAS_ROOT"
"$PYTHON" -m reanalysis_pipeline.parallel_link merge \
  --source "$SOURCE" --output-root "$OUTPUT_ROOT" --run-root "$RUN_ROOT"
"$PYTHON" -m reanalysis_pipeline.match \
  --source "$SOURCE" --linked "$OUTPUT_ROOT/$SOURCE-parallel-linked.csv" \
  --era5 "$ERA5" --output "$ASSET"
"$PYTHON" -m reanalysis_pipeline.native \
  --source "$SOURCE" --linked "$OUTPUT_ROOT/$SOURCE-parallel-linked.csv" \
  --output "$NATIVE"
exec "$PYTHON" -m reanalysis_pipeline.publish \
  --output "$PUBLIC_ROOT" "--$SOURCE" "$ASSET" "--$SOURCE-native" "$NATIVE"
