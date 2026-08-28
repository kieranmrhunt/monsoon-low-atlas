#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=112G
#SBATCH --time=08:00:00
#SBATCH --job-name=mla56-assets
#SBATCH --output=hpc-logs/mla56-assets-%j.out
#SBATCH --error=hpc-logs/mla56-assets-%j.err

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
PIPELINE_ROOT="$ATLAS_ROOT/../lps-v5.3-continuity-framework"
RELEASE_ROOT="$PIPELINE_ROOT/production/v5.6"
PYTHON="${LPS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
CATALOGUE="$RELEASE_ROOT/public-release/lps_v5.6-era5-1940-2025-core.parquet"
DOCUMENTS="$RELEASE_ROOT/release-documents"
DOCUMENT_STEM="lps-v5.6-era5-1940-2025-core"
TEMPLATE_CORE="${LPS_TEMPLATE_CORE:-$ATLAS_ROOT/assets/atlas-core.a3eb97565f4a.json.gz}"
IBTRACS_DIR="$PIPELINE_ROOT/inputs/ibtracs_v04r01"
RAINFALL_DIR="${LPS_IMD_RAINFALL_DIR:-/home/users/kieran/ncas/data/IMD-gauge}"

for path in "$CATALOGUE" "$TEMPLATE_CORE" \
  "$IBTRACS_DIR/ibtracs.NI.list.v04r01.csv" \
  "$IBTRACS_DIR/ibtracs.WP.list.v04r01.csv" \
  "$ATLAS_ROOT/inputs/climate/BSISO.INDEX.NORM.LY.data" \
  "$ATLAS_ROOT/inputs/climate/rmm.74toRealtime.txt" \
  "$ATLAS_ROOT/inputs/climate/oni.ascii.txt"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing v5.6 atlas input: $path" >&2
    exit 1
  fi
done

mkdir -p "$ATLAS_ROOT/assets" "$ATLAS_ROOT/data" "$ATLAS_ROOT/figures" "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
rsync -a "$DOCUMENTS/$DOCUMENT_STEM.metadata.json" \
  "$DOCUMENTS/$DOCUMENT_STEM.qa.json" \
  "$DOCUMENTS/$DOCUMENT_STEM.completion-audit.json" \
  "$DOCUMENTS/$DOCUMENT_STEM.calibration-protocol.json" \
  "$DOCUMENTS/$DOCUMENT_STEM.release-manifest.json" \
  "$DOCUMENTS/$DOCUMENT_STEM.quality-report.md" \
  data/

"$PYTHON" scripts/build_ibtracs_crosswalk.py \
  --parquet "$CATALOGUE" \
  --ibtracs "$IBTRACS_DIR/ibtracs.NI.list.v04r01.csv" \
  --ibtracs "$IBTRACS_DIR/ibtracs.WP.list.v04r01.csv" \
  --catalogue-version v5.6 \
  --output "data/lps-v5.6-ibtracs-v04r01-crosswalk.json"

"$PYTHON" scripts/build_v55_assets.py \
  --parquet "$CATALOGUE" \
  --release-manifest "data/$DOCUMENT_STEM.release-manifest.json" \
  --metadata "data/$DOCUMENT_STEM.metadata.json" \
  --qa "data/$DOCUMENT_STEM.qa.json" \
  --completion-audit "data/$DOCUMENT_STEM.completion-audit.json" \
  --protocol-amendment "data/$DOCUMENT_STEM.calibration-protocol.json" \
  --template-core "$TEMPLATE_CORE" \
  --ibtracs-crosswalk "data/lps-v5.6-ibtracs-v04r01-crosswalk.json" \
  --selection-table "$RELEASE_ROOT/physical_event_selection.parquet" \
  --rainfall-grd-dir "$RAINFALL_DIR" \
  --output-dir assets

CORE_NAME="$($PYTHON -c "import json; print(json.load(open('assets/atlas-build-manifest.json'))['core'])")"
"$PYTHON" scripts/build_climate_filter_asset.py \
  --core "assets/$CORE_NAME" \
  --bsiso inputs/climate/BSISO.INDEX.NORM.LY.data \
  --rmm inputs/climate/rmm.74toRealtime.txt \
  --oni inputs/climate/oni.ascii.txt \
  --output-dir assets

"$PYTHON" scripts/build_track_diagnostic_figure.py \
  --data "$CATALOGUE" \
  --core "assets/$CORE_NAME" \
  --output figures/lps-v5.6-track-diagnostics.png

chmod 0644 assets/atlas-build-manifest.json assets/atlas-core.*.json.gz \
  assets/atlas-detail.*.json.gz assets/atlas-climate.*.json.gz \
  data/lps-v5.6-* figures/lps-v5.6-track-diagnostics.png
