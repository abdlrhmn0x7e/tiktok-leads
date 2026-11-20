"""Frozen quality metric: how the lead filter treats creators reviewers already judged.

usage (repo root): uv run python .audit/hillclimb/niche_gate/eval.py [labeled.jsonl]
labeled.jsonl holds bio + recent captions (TikTok creator embed, 2026-11-10) for
all 104 kept and 320 of the 572 rejected fitness leads pushed 2026-11-05..10.
Each one is rebuilt as a CandidateProfile and sent through the real
evaluate_candidate. Rejected leads are weighted 572/320 so the kept rate is the
one a reviewer would see.
"""

import json
import sys
from pathlib import Path

from tiktok_leads.filters import evaluate_candidate
from tiktok_leads.models import CandidateProfile

KEPT_TOTAL, REJECTED_TOTAL = 104, 572
path = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("labeled.jsonl")
rows = [json.loads(line) for line in open(path)]
passed = {1: 0, 0: 0}
total = {1: 0, 0: 0}
other_skips = 0
for row in rows:
    evaluation = evaluate_candidate(
        CandidateProfile(
            handle=row["handle"], profile_url="", niche="fitness", followers_count=10**6,
            recent_video_views=[10**6] * 5, bio=row["bio"], extra_emails=["reviewed@example.com"],
            recent_video_captions=[video["desc"] for video in row["videos"]],
        ),
        min_followers=10_000, min_average_views=10_000,
    )
    total[row["label"]] += 1
    passed[row["label"]] += evaluation.lead is not None
    other_skips += evaluation.skip_code not in (None, "off_niche")
kept = passed[1] / total[1] * KEPT_TOTAL
rejected = passed[0] / total[0] * REJECTED_TOTAL
print(
    f"creators={len(rows)} other_skips={other_skips} "
    f"kept_leads_passed={passed[1]}/{total[1]} ({passed[1] / total[1]:.0%}) "
    f"rejected_leads_passed={passed[0]}/{total[0]} ({passed[0] / total[0]:.0%}) "
    f"KEPT_RATE={kept / (kept + rejected):.3f} (no gate: {KEPT_TOTAL / (KEPT_TOTAL + REJECTED_TOTAL):.3f}) "
    f"leads_per_100_pushed_today={(kept + rejected) / (KEPT_TOTAL + REJECTED_TOTAL) * 100:.0f}"
)
