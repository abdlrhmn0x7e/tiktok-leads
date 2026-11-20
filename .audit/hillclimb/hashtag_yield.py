"""Good fitness leads per crawl minute, per hashtag, from all daemon logs."""
import collections, datetime as dt, glob, json, re, sys
Q = json.load(open("quality_table.json"))
def kr(v):
    r = Q.get(v, {"kept": 0, "rejected": 0}); return (r["kept"] + 2.4) / (r["kept"] + r["rejected"] + 5)
time = collections.Counter(); leads = collections.Counter()
for f in sorted(glob.glob("../../logs/daemon-2026-1*.log")) + glob.glob("../../logs/hc-*.log"):
    via = prev = None
    for line in open(f, errors="ignore"):
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
        if not m: continue
        t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if via and prev and (t - prev).total_seconds() < 120: time[via] += (t - prev).total_seconds()
        prev = t
        if mm := re.search(r"crawling hashtag #(\S+) for niche=(\S+)", line):
            via = "#" + mm.group(1).lower() if mm.group(2) == "fitness" else None
        elif mm := re.search(r"searching users for '[^']+' \(niche=(\S+)", line):
            via = "search" if mm.group(1) == "fitness" else None
        elif "cycle done" in line or "starting cycle" in line or "handle(s) for niche" in line: via = None
        elif "inserted @" in line and via: leads[via] += 1
rows = sorted(((v, time[v] / 60, leads[v], leads[v] * kr(v) / (time[v] / 60)) for v in time if time[v] > 300), key=lambda r: -r[3])
tot_t = sum(r[1] for r in rows)
print(f"fitness overall good/min of crawl time: {sum(r[3]*r[1] for r in rows)/tot_t:.3f} ({len(rows)} sources, {tot_t:.0f} min)")
for frac in (0.25, 0.5):
    ct = cg = 0
    for v, t, l, g in rows:
        if ct >= frac * tot_t: break
        ct += t; cg += g * t
    print(f"top {int(frac*100)}% of crawl time: good/min={cg/ct:.3f}")
n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
for r in rows[:n] + [("...", 0, 0, 0)] + rows[-n:]: print(f"{r[0]:26} min={r[1]:6.1f} leads={r[2]:3} good/min={r[3]:.3f}")
