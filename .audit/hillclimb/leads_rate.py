"""Frozen hillclimb metric: fitness leads per wall-clock minute from a daemon log.

usage: python3 leads_rate.py LOG [--since 'YYYY-MM-DD HH:MM:SS'] [--minutes N]
Window starts at --since (default: the last 'daemon starting' line) and ends
--minutes later or at the last line. Prints the metric plus work and error counts.
"""

import argparse
import json
import re
from pathlib import Path
from datetime import datetime, timedelta

STAMP = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


QUALITY = json.load(open(Path(__file__).with_name("quality_table.json")))
PRIOR, PRIOR_WEIGHT = 0.48, 5  # fitness TikTok kept rate in leads-hub


def kept_rate(via):
    row = QUALITY.get(via or "", {"kept": 0, "rejected": 0})
    return (row["kept"] + PRIOR * PRIOR_WEIGHT) / (row["kept"] + row["rejected"] + PRIOR_WEIGHT)


def parse(line):
    m = STAMP.match(line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--since")
    ap.add_argument("--minutes", type=float)
    a = ap.parse_args()
    lines = [(parse(l), l) for l in open(a.log, errors="ignore")]
    lines = [(t, l) for t, l in lines if t]
    start = datetime.strptime(a.since, "%Y-%m-%d %H:%M:%S") if a.since else max(
        t for t, l in lines if "daemon starting" in l
    )
    end = min(start + timedelta(minutes=a.minutes), lines[-1][0]) if a.minutes else lines[-1][0]
    w = [(t, l) for t, l in lines if start <= t <= end]
    c = lambda s: sum(s in l for _, l in w)
    sleep = sum(
        int(m.group(1)) / (60 if m.group(2) == "s" else 1)
        for _, l in w
        if (m := re.search(r"sleeping (\d+)(min|s)\b", l))
    )
    backoff = sum(int(m.group(1)) for _, l in w if (m := re.search(r"backing off (\d+)min", l)))
    minutes = (end - start).total_seconds() / 60
    leads = c("inserted @")
    good = 0.0
    via = None
    for _, l in w:
        if m := re.search(r"crawling hashtag #(\S+)", l):
            via = f"#{m.group(1).lower()}"
        elif m := re.search(r"searching users for '([^']+)'", l):
            via = f"search:{m.group(1).lower()}"
        elif "handle(s) for niche" in l:
            via = "recheck"
        elif "inserted @" in l:
            good += kept_rate(via)
    print(
        f"window={start:%H:%M}-{end:%H:%M} minutes={minutes:.0f} leads={leads} "
        f"leads_per_min={leads / max(minutes, 1e-9):.3f} good_leads~{good:.1f} "
        f"GOOD_PER_MIN={good / max(minutes, 1e-9):.3f} new_checked={c('runner: checking @')} "
        f"already_seen={c('handle already evaluated')} cycles={c('cycle done')} searches={c('searching users')} "
        f"hashtags={c('crawling hashtag')} profile_fetches_blocked={c('blocked')} blocks={c('TikTok is blocking')} "
        f"sleep_min~{sleep:.0f} backoff_min~{backoff} errors={c(' ERROR ')}"
    )


main()
