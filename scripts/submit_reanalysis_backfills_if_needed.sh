#!/bin/bash
set -u

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ATLAS_ROOT"
mkdir -p hpc-logs

submit_if_incomplete() {
	local finalizer_file="$1"
	local submit_script="$2"
	if [[ -s "$finalizer_file" ]]; then return 0; fi
	if ! /usr/bin/bash "$submit_script"; then
		echo "$(date -Is) submission deferred: $submit_script" >&2
	fi
}

submit_if_incomplete ".reanalysis-runs/erainterim-full-197901-201908/finalizer.job-id" "scripts/submit_era_interim_backfill.sh"
submit_if_incomplete ".reanalysis-runs/jra55-full-195801-202401/finalizer.job-id" "scripts/submit_jra55_backfill.sh"
if [[ ! -s ".reanalysis-runs/merra2-full-1980-01-2026-07/finalizer.job-id" ]]; then
	if ! LPS_MERRA2_END_MONTH=2026-07 /usr/bin/bash scripts/submit_merra2_backfill.sh; then
		echo "$(date -Is) submission deferred: scripts/submit_merra2_backfill.sh" >&2
	fi
fi
