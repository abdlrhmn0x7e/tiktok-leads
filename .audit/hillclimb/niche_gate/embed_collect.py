"""Fetch bio + recent captions from TikTok's public creator embed. usage: python3 embed_collect.py LABELED.json OUT.jsonl"""
import json, re, subprocess, sys, time
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
STATE = re.compile(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', re.S)

def fetch(handle):
    for attempt in range(4):
        r = subprocess.run(["curl", "-s", "--compressed", "-m", "25", "-A", UA, "-H", "Accept-Language: en-US,en;q=0.9",
                            "-w", "\n%{http_code}", f"https://www.tiktok.com/embed/@{handle}"], capture_output=True, text=True)
        body, _, status = r.stdout.rpartition("\n")
        m = STATE.search(body)
        if status == "200" and m:
            v = next(iter(json.loads(m.group(1))["source"]["data"].values()))
            return v.get("userInfo") or {}, v.get("videoList") or [], None
        time.sleep(3 * (attempt + 1))
    return None, None, f"http {status} len {len(body)}"

sample = json.load(open(sys.argv[1]))
try: done = {json.loads(l)["handle"] for l in open(sys.argv[2])}
except FileNotFoundError: done = set()
errs = 0
with open(sys.argv[2], "a") as out:
    for handle, label, via in sample:
        if handle in done: continue
        ui, vl, err = fetch(handle)
        if err:
            errs += 1; print("fail", handle, err, flush=True)
            if errs >= 8: print("stopping", flush=True); break
            continue
        errs = 0
        out.write(json.dumps({"handle": handle, "label": label, "via": via, "bio": ui.get("signature", ""), "nick": ui.get("nickname", ""),
                              "followers": ui.get("followerCount"), "code": ui.get("code"),
                              "videos": [{"desc": x.get("desc", ""), "plays": x.get("playCount")} for x in vl]}) + "\n"); out.flush()
        time.sleep(1.2)
print("done", flush=True)
