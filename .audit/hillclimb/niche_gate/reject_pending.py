"""Reject pending fitness leads that fail the content gate, through leads-hub's own reject mutation.

usage (repo root): uv run python .audit/hillclimb/niche_gate/reject_pending.py LEADS_EXPORT.jsonl PROFILES.jsonl [--apply]
Without --apply it only prints what it would do. Each lead's status is read
again right before the write, so a lead reviewed in the meantime is left alone.
Every rejection is appended to auto_rejected.jsonl beside this file: the undo
list, and the list to exclude when measuring reviewer acceptance.
undo one: npx convex run leads:restoreFromExtension '{"id":"<id>"}'  (in leads-hub/packages/backend)
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tiktok_leads.niches import is_on_niche

BACKEND = "/Users/abdlrhmn/Projects/leads-hub/packages/backend"
LOG = Path(__file__).with_name("auto_rejected.jsonl")


def convex(function: str, lead_id: str) -> str:
    result = subprocess.run(
        ["npx", "convex", "run", function, json.dumps({"id": lead_id})],
        cwd=BACKEND, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-300:])
    return result.stdout


def reject(lead: dict) -> str:
    try:
        current = json.loads(convex("leads:getForExtension", lead["_id"]))
        if current is None or current["status"] != "pending":
            return "skipped: no longer pending"
        convex("leads:rejectFromExtension", lead["_id"])
    except Exception as error:  # one failed lead must not stop the batch; it stays pending
        return f"failed: {error}"
    return "rejected"


leads = {}
for line in open(sys.argv[1]):
    lead = json.loads(line)
    if lead["status"] == "pending" and lead["platform"] == "tiktok" and lead["niche"] == "fitness":
        leads[lead["handle"]] = lead
off_niche, on_niche, unjudged = [], [], 0
for line in open(sys.argv[2]):
    profile = json.loads(line)
    lead = leads.get(profile["handle"])
    if lead is None:
        continue
    if not profile["videos"]:
        unjudged += 1  # private or removed account: nothing to judge, stays pending
    elif is_on_niche("fitness", profile["bio"], [video["desc"] for video in profile["videos"]]):
        on_niche.append(lead)
    else:
        off_niche.append(lead)
print(f"pending fitness TikTok leads judged: off-niche={len(off_niche)} on-niche={len(on_niche)} no visible videos={unjudged}")
if "--apply" not in sys.argv:
    print("dry run. off-niche sample:", ", ".join("@" + lead["handle"] for lead in off_niche[:12]))
    print("on-niche sample:", ", ".join("@" + lead["handle"] for lead in on_niche[:12]))
    raise SystemExit
counts: dict[str, int] = {}
with ThreadPoolExecutor(4) as pool, LOG.open("a") as log:
    for lead, outcome in zip(off_niche, pool.map(reject, off_niche)):
        counts[outcome.split(":")[0]] = counts.get(outcome.split(":")[0], 0) + 1
        if outcome == "rejected":
            log.write(json.dumps({"id": lead["_id"], "handle": lead["handle"], "email": lead["email"]}) + "\n")
            log.flush()
        elif outcome.startswith("failed"):
            print(lead["handle"], outcome, flush=True)
print(counts)
