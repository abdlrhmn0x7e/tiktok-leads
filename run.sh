#!/usr/bin/env bash
# Keep the tiktok-leads daemon alive forever.
# If the process ever exits (crash, OOM, killed browser), relaunch it.
#
# Usage:
#   ./run.sh                # defaults to the "fitness" niche
#   ./run.sh fitness,mom    # comma-separated niches
#
# Each run also:
#   - tees daemon output to logs/daemon-YYYY-MM-DD.log
#   - keeps the Mac awake while the daemon runs (caffeinate)
#   - snapshots the SQLite database once a day to data/backups/ (keeps 14)
#
# Stop with Ctrl-C.
set -u

cd "$(dirname "$0")"

NICHES="${1:-fitness}"
RESTART_DELAY="${RESTART_DELAY:-60}"

mkdir -p logs data/backups

# Prevent idle/system sleep from silently pausing the scraper (macOS only).
KEEP_AWAKE=""
if command -v caffeinate >/dev/null 2>&1; then
  KEEP_AWAKE="caffeinate -is"
fi

backup_database() {
  local today="$1"
  local target="data/backups/leads-${today}.sqlite"
  if [ -f data/leads.sqlite ] && [ ! -f "${target}" ]; then
    if sqlite3 data/leads.sqlite ".backup '${target}'" 2>/dev/null; then
      echo "[run.sh] backed up database to ${target}"
      # Keep the newest 14 snapshots.
      ls -1t data/backups/leads-*.sqlite 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null || true
    fi
  fi
}

trap 'echo "[run.sh] stopping"; exit 0' INT TERM

while true; do
  TODAY="$(date '+%Y-%m-%d')"
  backup_database "${TODAY}"
  echo "[run.sh] $(date '+%Y-%m-%d %H:%M:%S') starting daemon for niches=${NICHES} (log: logs/daemon-${TODAY}.log)"
  ${KEEP_AWAKE} uv run tiktok-leads --daemon --niche "${NICHES}" 2>&1 | tee -a "logs/daemon-${TODAY}.log" || true
  echo "[run.sh] $(date '+%Y-%m-%d %H:%M:%S') daemon exited; restarting in ${RESTART_DELAY}s"
  sleep "${RESTART_DELAY}"
done
