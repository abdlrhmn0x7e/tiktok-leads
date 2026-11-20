"""Expected reviewer kept-rate of one fitness sweep queue, from leads-hub history.

usage (inside a worktree): uv run python /path/queue_quality.py DB_COPY
Builds the real _build_queue against a copy of the database with Convex search
terms disabled, 20 times (trending slots are random), and weights each work item
by the smoothed kept rate of leads that source surfaced before.
"""

import json
import statistics
import sys
from pathlib import Path

from tiktok_leads import daemon
from tiktok_leads.db import LeadRepository
from tiktok_leads.settings import Settings

sys.path.insert(0, str(Path(__file__).parent))
QUALITY = json.load(open(Path(__file__).with_name("quality_table.json")))


def kept_rate(via):
    row = QUALITY.get(via, {"kept": 0, "rejected": 0})
    return (row["kept"] + 0.48 * 5) / (row["kept"] + row["rejected"] + 5)


daemon.fetch_search_terms = lambda settings: []
settings = Settings(database_path=Path(sys.argv[1]))
repo = LeadRepository(settings.database_path)
repo.initialize()
means, sizes = [], []
for _ in range(20):
    queue = daemon._build_queue(settings, repo, ["fitness"])
    rates = [
        kept_rate(f"#{i.value.lower()}" if i.kind == "hashtag" else f"search:{i.value.lower()}")
        for i in queue
        if i.kind in ("hashtag", "search")
    ]
    means.append(statistics.mean(rates))
    sizes.append(len(queue))
print(f"items={statistics.median(sizes):.0f} expected_kept_rate median={statistics.median(means):.3f} "
      f"range={min(means):.3f}-{max(means):.3f} n=20")
