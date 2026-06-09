#!/usr/bin/env bash
# Keep the tiktok-leads daemon alive forever.
# If the process ever exits (crash, OOM, killed browser), relaunch it.
#
# Usage:
#   ./run.sh                # defaults to the "fitness" niche
#   ./run.sh fitness,mom    # comma-separated niches
#
# Stop with Ctrl-C.
set -u

cd "$(dirname "$0")"

NICHES="${1:-fitness}"
RESTART_DELAY="${RESTART_DELAY:-60}"

trap 'echo "[run.sh] stopping"; exit 0' INT TERM

while true; do
  echo "[run.sh] $(date '+%Y-%m-%d %H:%M:%S') starting daemon for niches=${NICHES}"
  uv run tiktok-leads --daemon --niche "${NICHES}" || true
  echo "[run.sh] $(date '+%Y-%m-%d %H:%M:%S') daemon exited; restarting in ${RESTART_DELAY}s"
  sleep "${RESTART_DELAY}"
done
