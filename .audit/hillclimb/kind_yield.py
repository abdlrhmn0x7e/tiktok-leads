"""Leads and wall time per work-item kind in a daemon log window."""
import collections, datetime as dt, re, sys
log, since = sys.argv[1], sys.argv[2]
tm = collections.Counter(); runs = collections.Counter(); leads = collections.Counter(); cur = None; labels = ""; prev = None
for line in open(log, errors="ignore"):
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
    if not m or m.group(1) < since: continue
    t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    if prev and cur: tm[cur] += (t - prev).total_seconds()
    prev = t
    if "starting cycle:" in line: labels = line.split("starting cycle:")[1]
    if mm := re.search(r"crawling hashtag #(\S+)", line):
        tag = mm.group(1); cur = "harvested" if f"#{tag}*" in labels else ("trending" if f"#{tag}[trend]" in labels else "seed"); runs[cur] += 1
    elif "searching users" in line: cur = "search"; runs[cur] += 1
    elif "handle(s) for niche" in line: cur = "yahoo/recheck"; runs[cur] += 1
    elif "cycle done" in line: cur = "rest"
    elif "inserted @" in line and cur: leads[cur] += 1
T = sum(tm.values()) or 1
for k in tm: print(f"{k:14} runs={runs[k]:4} leads={leads[k]:4} time={tm[k]/60:5.1f}min ({tm[k]/T:4.0%}) leads/min={leads[k]/(tm[k]/60 or 1):.2f}")
