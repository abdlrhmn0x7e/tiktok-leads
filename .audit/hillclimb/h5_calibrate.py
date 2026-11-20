"""Pair each 'feed signals' line with that creator's outcome; test likes-per-video cutoffs."""
import glob, re, sys
sig, out = {}, {}
for f in sys.argv[1:]:
    for line in open(f, errors="ignore"):
        if m := re.search(r"feed signals @(\S+) hearts=(\S+) videos=(\S+) surfacing_plays=(\S+)", line):
            h, hearts, vids, plays = m.groups()
            if hearts != "None" and vids not in ("None", "0"):
                sig[h] = (int(hearts) / int(vids), int(plays) if plays != "None" else None)
        elif m := re.search(r"inserted @(\S+)", line): out[m.group(1)] = "lead"
        elif m := re.search(r"skipped @(\S+): average views below", line): out[m.group(1)] = "low_views"
        elif m := re.search(r"skipped @(\S+): (no email|email already)", line): out.setdefault(m.group(1), "other")
pairs = [(sig[h][0], sig[h][1], out[h]) for h in sig if h in out and out[h] != "other"]
leads = [p for p in pairs if p[2] == "lead"]; low = [p for p in pairs if p[2] == "low_views"]
print(f"samples: {len(pairs)} (leads {len(leads)}, low_views {len(low)})")
print("lead likes/video sorted:", sorted(round(p[0]) for p in leads)[:15])
for cut in (50, 100, 150, 200, 300, 500, 800):
    lost = sum(p[0] < cut for p in leads); saved = sum(p[0] < cut for p in low)
    print(f"skip if likes/video < {cut:4}: drops {lost}/{len(leads)} leads, saves {saved}/{len(low)} wasted fetches")
