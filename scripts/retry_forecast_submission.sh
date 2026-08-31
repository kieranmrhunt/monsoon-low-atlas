#!/bin/bash
set -u

ATLAS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="${1:?retry label is required}"
shift
if (( $# == 0 )); then
  echo "a submission command is required" >&2
  exit 2
fi
MAX_ATTEMPTS="${LPS_SLURM_RETRY_ATTEMPTS:-720}"
POLL_SECONDS="${LPS_SLURM_RETRY_POLL_SECONDS:-60}"
LOG="$ATLAS_ROOT/hpc-logs/${LABEL}-submission-retry.log"

mkdir -p "$ATLAS_ROOT/hpc-logs"
cd "$ATLAS_ROOT"
for (( ATTEMPT=1; ATTEMPT<=MAX_ATTEMPTS; ATTEMPT++ )); do
  if scontrol ping 2>/dev/null | grep -q 'is UP'; then
    echo "$(date -u -Is) Slurm controller available; submitting $LABEL" >> "$LOG"
    if "$@" >> "$LOG" 2>&1; then
      echo "$(date -u -Is) $LABEL submission succeeded" >> "$LOG"
      exit 0
    fi
    echo "$(date -u -Is) $LABEL submission attempt failed; retrying" >> "$LOG"
  elif (( ATTEMPT == 1 || ATTEMPT % 30 == 0 )); then
    echo "$(date -u -Is) waiting for the Slurm controller ($ATTEMPT/$MAX_ATTEMPTS)" >> "$LOG"
  fi
  sleep "$POLL_SECONDS"
done

echo "$(date -u -Is) $LABEL submission did not succeed within the retry window" >> "$LOG"
exit 1
