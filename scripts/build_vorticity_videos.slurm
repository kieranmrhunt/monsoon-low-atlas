#!/bin/bash
#SBATCH --account=ncas_climate
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:40:00
#SBATCH --job-name=mla-weather
#SBATCH --array=0-621%50
#SBATCH --output=../hpc-logs/mla-weather-%A_%a.out

set -euo pipefail

ATLAS_ROOT="${SLURM_SUBMIT_DIR}"
PYTHON="${LPS_PYTHON:-/home/users/kieran/miniconda3/envs/py311/bin/python}"
MONTH_MANIFEST="${1:-${ATLAS_ROOT}/data/vorticity-active-months.csv}"
OUTPUT_DIR="${2:-${ATLAS_ROOT}/../lps-v5.3-continuity-framework/production/v5.4.2/atlas-weather-v5.4.2-r2}"
FIELD="${3:-vorticity}"

export MPLCONFIGDIR="${LPS_MPLCONFIGDIR:-${TMPDIR:-/tmp}/mla-mpl-${SLURM_JOB_ID}}"
export XDG_CACHE_HOME="${TMPDIR:-/tmp}/mla-cache-${SLURM_JOB_ID}"
mkdir -p "$MPLCONFIGDIR" "$XDG_CACHE_HOME"
if [[ -f /home/users/kieran/.cache/matplotlib/fontlist-v390.json ]]; then
	cp /home/users/kieran/.cache/matplotlib/fontlist-v390.json "$MPLCONFIGDIR/"
fi

cd "$ATLAS_ROOT"
"$PYTHON" scripts/build_vorticity_videos.py \
	--field "$FIELD" \
	--month-manifest "$MONTH_MANIFEST" \
	--task-id "${SLURM_ARRAY_TASK_ID}" \
	--output-dir "$OUTPUT_DIR"
