#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${LPS_REANALYSIS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
LEDGER="${LPS_IMDAA_LEDGER:-$ATLAS_ROOT/data/reanalyses/imdaa/rds-ledger.json}"
OUTPUT_ROOT="${LPS_IMDAA_RAW_ROOT:-$ATLAS_ROOT/data/reanalyses/imdaa/raw}"
MAXIMUM_ACTIVE="${LPS_IMDAA_MAXIMUM_ACTIVE:-8}"
cd "$ATLAS_ROOT"
exec "$PYTHON" -m reanalysis_pipeline.pump_imdaa \
  --ledger "$LEDGER" --output-root "$OUTPUT_ROOT" --maximum-active "$MAXIMUM_ACTIVE"
