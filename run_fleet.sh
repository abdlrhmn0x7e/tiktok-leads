#!/usr/bin/env bash
# Launch the 4-worker parallel US proxy fleet:
#   - 1 dedicated following-list worker (--only following)
#   - 3 sharded hashtag workers (--only hashtags --shard 0/3, 1/3, 2/3)
# Each worker gets its own disjoint pool of 4 fast US Webshare proxies.
set -euo pipefail

cd "$(dirname "$0")"
export TMPDIR=/tmp
mkdir -p logs data/backups

NICHE="${1:-fitness}"
LIMIT="${LIMIT:-150}"
TODAY="$(date '+%Y-%m-%d')"

# Stop any previously running fleet or single daemon cleanly
pkill -f "tiktok-leads --daemon" 2>/dev/null || true
pkill -f "bash ./run.sh" 2>/dev/null || true
sleep 2

start_worker() {
  local name="$1"
  local proxies="$2"
  shift 2
  local logfile="logs/${name}-${TODAY}.log"
  PROXY_SERVERS="${proxies}" TIKTOK_NUM_SESSIONS=2 \
    nohup bash -c "
      export TMPDIR=/tmp
      while true; do
        echo \"[${name}] \$(date '+%Y-%m-%d %H:%M:%S') starting (proxies=${proxies})\"
        caffeinate -is uv run tiktok-leads --daemon --niche '${NICHE}' --limit '${LIMIT}' $*
        echo \"[${name}] \$(date '+%Y-%m-%d %H:%M:%S') exited; restarting in 30s\"
        sleep 30
      done
    " >> "${logfile}" 2>&1 < /dev/null &
  echo "Started ${name} PID=$! -> ${logfile}"
  sleep 12
}

# 16 fastest verified US Webshare proxies (all <2.4s to tiktok.com), 4 per worker
start_worker "following"  "217.117.164.177:6819,152.232.123.237:6122,38.154.183.73:7841,213.169.212.96:6736" --only following
start_worker "hashtags-0" "173.0.9.89:5672,152.232.103.128:6028,46.202.68.110:7855,82.23.206.62:5868"         --only hashtags --shard 0/3
start_worker "hashtags-1" "23.95.244.172:6125,104.164.49.41:7696,45.61.122.77:6369,154.12.132.24:6154"        --only hashtags --shard 1/3
start_worker "hashtags-2" "31.57.86.193:5575,104.168.118.44:6000,107.175.208.201:6142,104.239.124.24:6302"    --only hashtags --shard 2/3
