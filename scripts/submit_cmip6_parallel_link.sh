#!/bin/bash
set -euo pipefail

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ROOT="${1:?CMIP6 paired run root is required}"
CONCURRENCY="${LPS_CMIP6_LINK_CONCURRENCY:-80}"
PYTHON="${LPS_CMIP6_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
LINK_TABLE="$RUN_ROOT/link.tsv"
[[ -s "$LINK_TABLE" ]]
mkdir -p "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"

MERGE_IDS=()
JOB_TABLE="$RUN_ROOT/parallel-link-job-ids.tsv"
JOB_TABLE_TEMP="$JOB_TABLE.part-$$"
printf 'source_label\tblocks_job_id\tmerge_job_id\n' > "$JOB_TABLE_TEMP"
while IFS=$'\t' read -r INDEX SOURCE_LABEL TRACKING_ROOT LINK_ROOT; do
  [[ "$INDEX" =~ ^[0-9]+$ ]]
  MANIFEST="$($PYTHON -m reanalysis_pipeline.parallel_link prepare \
    --source "$SOURCE_LABEL" --output-root "$TRACKING_ROOT" --run-root "$LINK_ROOT")"
  COUNT="$($PYTHON -c 'import csv,sys; print(sum(1 for _ in csv.DictReader(open(sys.argv[1]))))' "$MANIFEST")"
  LAST=$((COUNT - 1))
  BLOCK_ID="$(sbatch --parsable --array="0-$LAST%$CONCURRENCY" \
    scripts/cmip6_link_block.slurm "$LINK_ROOT")"
  MERGE_ID="$(sbatch --parsable --dependency="afterok:$BLOCK_ID" \
    scripts/cmip6_link_merge.slurm "$SOURCE_LABEL" "$TRACKING_ROOT" "$LINK_ROOT")"
  MERGE_IDS+=("$MERGE_ID")
  printf '%s\t%s\t%s\n' "$SOURCE_LABEL" "$BLOCK_ID" "$MERGE_ID" >> "$JOB_TABLE_TEMP"
  printf '%s\t%s\t%s\t%s\n' "$SOURCE_LABEL" "$COUNT" "$BLOCK_ID" "$MERGE_ID"
done < "$LINK_TABLE"
mv "$JOB_TABLE_TEMP" "$JOB_TABLE"

DEPENDENCY="afterok:$(IFS=:; printf '%s' "${MERGE_IDS[*]}")"
PHYSICS_ID="$(sbatch --parsable --dependency="$DEPENDENCY" \
  scripts/dispatch_cmip6_physics.slurm "$RUN_ROOT")"
printf '%s\n' "$PHYSICS_ID" > "$RUN_ROOT/physics-dispatch.job-id"
printf 'physics-dispatch\t%s\n' "$PHYSICS_ID"
