#!/bin/bash
# Resume MERRA-2 detection/linking after every standard month has validated.
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FULL_RUN="${1:?existing full-period run directory is required}"
DATA_ROOT="${LPS_MERRA2_ROOT:-$ATLAS_ROOT/data/reanalyses/merra2}"
OUTPUT_ROOT="$DATA_ROOT/tracking"
MONTH_CONCURRENCY="${LPS_MERRA2_MONTH_CONCURRENCY:-72}"
LINK_CONCURRENCY="${LPS_REANALYSIS_LINK_CONCURRENCY:-24}"

cd "$ATLAS_ROOT"
FULL_RUN="$(cd "$FULL_RUN" && pwd)"
MONTHS="$FULL_RUN/months.tsv"
[[ -s "$MONTHS" ]]
MONTH_COUNT="$(wc -l < "$MONTHS")"
FIRST_MONTH="$(head -1 "$MONTHS" | cut -f2)"
LAST_MONTH="$(tail -1 "$MONTHS" | cut -f2)"
YEAR_COUNT="$((10#${LAST_MONTH:0:4} - 10#${FIRST_MONTH:0:4} + 1))"

# Standardisation jobs validate every monthly product before returning zero.
# Requiring a complete file inventory here prevents an accidental partial
# tracking run when this script is used independently of that array.
for KIND in vorticity surface precipitation provenance; do
  EXTENSION="nc"
  [[ "$KIND" == provenance ]] && EXTENSION="json"
  COUNT="$(find "$DATA_ROOT/standard/$KIND" -maxdepth 1 -type f -name "*.$EXTENSION" | wc -l)"
  if [[ "$COUNT" -lt "$MONTH_COUNT" ]]; then
    echo "MERRA-2 $KIND has $COUNT/$MONTH_COUNT monthly products; tracking not resumed." >&2
    exit 1
  fi
done
COUNT="$(find "$DATA_ROOT/standard/auxiliary" -maxdepth 1 -type f -name 'pl3h-*.nc' | wc -l)"
if [[ "$COUNT" -lt "$MONTH_COUNT" ]]; then
  echo "MERRA-2 auxiliary data have $COUNT/$MONTH_COUNT monthly products; tracking not resumed." >&2
  exit 1
fi

ACTIVE_JOB_IDS="$(squeue -h -u "$USER" -o '%i')"
for MARKER in detect parallel-prepare parallel-years parallel-finalizer; do
  if [[ -s "$FULL_RUN/$MARKER.job-id" ]]; then
    OLD_ID="$(head -1 "$FULL_RUN/$MARKER.job-id")"
    while IFS= read -r ACTIVE_ID; do
      case "$ACTIVE_ID" in
        "$OLD_ID"|"${OLD_ID}_"*)
          echo "Existing $MARKER job $OLD_ID is still active; not starting a duplicate." >&2
          exit 1
          ;;
      esac
    done <<< "$ACTIVE_JOB_IDS"
  fi
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
AUDIT_ROOT="$FULL_RUN/tracking-resumes/$STAMP"
LINK_ROOT="$AUDIT_ROOT/parallel-link"
mkdir -p "$AUDIT_ROOT/previous-job-ids" "$ATLAS_ROOT/hpc-logs"
for MARKER in detect parallel-prepare parallel-years parallel-finalizer; do
  if [[ -f "$FULL_RUN/$MARKER.job-id" ]]; then
    cp -p "$FULL_RUN/$MARKER.job-id" "$AUDIT_ROOT/previous-job-ids/$MARKER.job-id"
  fi
done

record() {
  printf '%s\t%s\n' "$1" "$2" >> "$AUDIT_ROOT/submitted-jobs.tsv"
  printf '%s\n' "$2" > "$FULL_RUN/$1.job-id"
}

DETECT_ID="$(sbatch --parsable --kill-on-invalid-dep=yes \
  --array="1-$MONTH_COUNT%$MONTH_CONCURRENCY" \
  scripts/detect_reanalysis_month.slurm merra2 "$MONTHS" "$DATA_ROOT" "$OUTPUT_ROOT")"
record detect "$DETECT_ID"
PREPARE_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$DETECT_ID" \
  scripts/prepare_parallel_reanalysis_link.slurm merra2 "$OUTPUT_ROOT" "$LINK_ROOT")"
record parallel-prepare "$PREPARE_ID"
LINK_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$PREPARE_ID" \
  --array="0-$((YEAR_COUNT - 1))%$LINK_CONCURRENCY" \
  scripts/link_reanalysis_year.slurm "$LINK_ROOT")"
record parallel-years "$LINK_ID"
FINAL_ID="$(sbatch --parsable --kill-on-invalid-dep=yes --dependency="afterok:$LINK_ID" \
  scripts/finalize_parallel_reanalysis.slurm merra2 "$DATA_ROOT" "$OUTPUT_ROOT" "$LINK_ROOT")"
record parallel-finalizer "$FINAL_ID"

printf 'MERRA-2 %s--%s: detect %s; prepare %s; link %s; finalizer %s\n' \
  "$FIRST_MONTH" "$LAST_MONTH" "$DETECT_ID" "$PREPARE_ID" "$LINK_ID" "$FINAL_ID"
printf 'Resume audit: %s\n' "$AUDIT_ROOT"
