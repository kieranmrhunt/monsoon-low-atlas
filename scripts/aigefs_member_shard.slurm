#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --job-name=mla-aigefs-mem
#SBATCH --output=hpc-logs/mla-aigefs-mem-%A_%a.out
#SBATCH --error=hpc-logs/mla-aigefs-mem-%A_%a.err

set -euo pipefail
ATLAS_ROOT="${SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is required}"
JOBS="${1:?job table is required}"
RUN_ROOT="${2:?run root is required}"
PYTHON="${LPS_FORECAST_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
ROW="$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$JOBS")"
IFS=$'\t' read -r INDEX CYCLE MEMBER <<< "$ROW"
[[ "$CYCLE" =~ ^[0-9]{10}$ && "$MEMBER" =~ ^p[0-9]{2}$ ]]
OUTPUT="$RUN_ROOT/members/$CYCLE/$MEMBER.npz"
if [[ -s "$OUTPUT" ]]; then
	echo "Reusing completed AIGEFS member shard $OUTPUT"
	exit 0
fi
cd "$ATLAS_ROOT"
exec "$PYTHON" -m forecast_pipeline.aigefs_shards member \
  --cycle "$CYCLE" \
  --member "$MEMBER" \
  --output "$OUTPUT"
