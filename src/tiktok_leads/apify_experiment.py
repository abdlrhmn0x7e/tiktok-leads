"""Compare Apify search configurations on small samples before spending the real budget.

Each sample is one search term under one sort/date configuration, `--sample-size`
results each. New creators that pass the same filters as tiktok-leads-apify are
then looked up with the scraper, read-only: nothing is saved to SQLite or sent
to Convex. The report shows, per sample, how many results turned into
qualified leads, plus each candidate's bio and recent hashtags so lead quality
can be judged by eye.

    uv run tiktok-leads-apify-experiment --dry-run
    uv run tiktok-leads-apify-experiment
    uv run tiktok-leads-apify-experiment --config most-liked:all-time --config latest:past-month \\
        --term "high protein" --term "hevy code" --sample-size 12
    uv run tiktok-leads-apify-experiment --items-file data/apify/experiments/<stamp>/items.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from tiktok_leads.apify_client import run_actor
from tiktok_leads.apify_discovery import (
    ACTOR,
    DATE_RANGES,
    SORTS,
    Candidate,
    actor_input,
    add_filter_args,
    pick_candidates,
)
from tiktok_leads.cli import configure_logging
from tiktok_leads.db import LeadRepository
from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.factory import build_source
from tiktok_leads.filters import evaluate_candidate
from tiktok_leads.models import CandidateProfile
from tiktok_leads.settings import Settings
from tiktok_leads.sources.tiktokapi_source import TikTokBlockedError

DEFAULT_CONFIGS = ["most-liked:all-time", "most-relevant:all-time", "most-liked:past-6-months", "latest:past-month"]
# One diet term, one macro/food term, one brand-partner term.
DEFAULT_TERMS = ["calorie deficit hack", "high protein", "hevy code"]


def parse_config(value: str) -> tuple[str, str]:
    sort, _, date_range = value.partition(":")
    if sort not in SORTS or date_range not in DATE_RANGES:
        raise argparse.ArgumentTypeError(
            f"{value!r}: expected SORT:DATE_RANGE with SORT in {sorted(SORTS)} and DATE_RANGE in {sorted(DATE_RANGES)}"
        )
    return sort, date_range


def parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        action="append",
        type=parse_config,
        help=f"SORT:DATE_RANGE to test (repeatable). Default: {', '.join(DEFAULT_CONFIGS)}",
    )
    parser.add_argument(
        "--term", action="append", help=f"Search term to test (repeatable). Default: {', '.join(DEFAULT_TERMS)}"
    )
    parser.add_argument("--niche", default="fitness", help="Niche passed to the profile lookup (default: fitness)")
    parser.add_argument("--sample-size", type=int, default=12, help="Apify results per term per config (default: 12)")
    parser.add_argument(
        "--max-charge-usd", type=float, default=1.0, help="Apify charge cap per config run (default: 1.0)"
    )
    add_filter_args(parser, settings)
    parser.add_argument("--no-verify", action="store_true", help="Skip the TikTok profile lookup")
    parser.add_argument("--items-file", type=Path, help="Re-analyze a saved experiment instead of running Apify")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned runs and exit without spending")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    args = parser.parse_args()
    args.config = args.config or [parse_config(c) for c in DEFAULT_CONFIGS]
    args.term = args.term or DEFAULT_TERMS
    return args


async def look_up(settings: Settings, handles: list[str], niche: str) -> dict[str, CandidateProfile]:
    profiles: dict[str, CandidateProfile] = {}
    try:
        async with build_source(settings) as source:
            async for profile in source.profiles_from_handles(handles, niche=niche):
                profiles[profile.handle.removeprefix("@").lower()] = profile
    except TikTokBlockedError as error:
        print(f"TikTok is blocking requests ({error}); {len(profiles)}/{len(handles)} profiles looked up.")
    return profiles


def write_report(
    out_dir: Path,
    samples: dict[tuple[str, str], list[dict]],
    candidates: dict[tuple[str, str], list[Candidate]],
    profiles: dict[str, CandidateProfile] | None,
    known: set[str],
    settings: Settings,
) -> Path:
    def qualified(handle: str) -> bool:
        profile = (profiles or {}).get(handle)
        return profile is not None and evaluate_candidate(
            profile, min_followers=settings.min_followers, min_average_views=settings.min_average_views
        ).lead is not None

    def has_email(profile: CandidateProfile) -> bool:
        return bool(extract_emails(profile.bio, *profile.external_links) or profile.extra_emails)

    lines = [
        "| config | term | results | creators | new | candidates | email | qualified | results/qualified |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for (config, term), items in samples.items():
        creators = {str((i.get("authorMeta") or {}).get("name") or "").lower() for i in items} - {""}
        picked = candidates[(config, term)]
        emailed = qualifying = "-"
        ratio = "-"
        if profiles is not None:
            emailed = sum(1 for c in picked if c.handle in profiles and has_email(profiles[c.handle]))
            qualifying = sum(1 for c in picked if qualified(c.handle))
            ratio = f"{len(items) / qualifying:.0f}" if qualifying else "inf"
        lines.append(
            f"| {config} | {term} | {len(items)} | {len(creators)} | {len(creators - known)} | "
            f"{len(picked)} | {emailed} | {qualifying} | {ratio} |"
        )

    lines += ["", "## Candidates", ""]
    captions = {
        (str((i.get("authorMeta") or {}).get("name") or "").lower(), i.get("searchQuery")): i
        for sample in samples.values()
        for i in sample
    }
    for (config, term), picked in candidates.items():
        for c in picked:
            item = captions.get((c.handle, term)) or {}
            lines.append(f"### @{c.handle} ({config}, {term!r})")
            lines.append(f"- followers {c.followers:,}, matching video {c.top_video_views:,} views: {item.get('webVideoUrl', '')}")
            lines.append(f"- caption: {(item.get('text') or '').strip()[:200]!r}")
            profile = (profiles or {}).get(c.handle)
            if profile is not None:
                avg = profile.average_views
                lines.append(
                    f"- profile: qualified={'yes' if qualified(c.handle) else 'no'}, email={'yes' if has_email(profile) else 'no'}, "
                    f"average views={avg if avg is not None else 'unknown'}"
                )
                lines.append(f"- bio: {profile.bio.strip()[:200]!r}")
                lines.append(f"- recent hashtags: {' '.join('#' + t for t in profile.discovered_hashtags[:15])}")
            elif profiles is not None:
                lines.append("- profile: lookup failed")
            lines.append("")

    report = out_dir / "report.md"
    report.write_text("\n".join(lines))
    return report


def main() -> int:
    settings = Settings()
    args = parse_args(settings)
    configure_logging(args.log_level)
    if settings.tiktok_suppress_library_errors:
        logging.getLogger("TikTokApi.tiktok").setLevel(logging.CRITICAL)

    if args.items_file:
        out_dir = args.items_file.parent
        items = json.loads(args.items_file.read_text())
    else:
        per_run = len(args.term) * args.sample_size
        if args.dry_run:
            for sort, date_range in args.config:
                print(f"{sort}:{date_range}  {per_run} results over {len(args.term)} terms, cap ${args.max_charge_usd:.2f}")
            print(f"total: {per_run * len(args.config)} results")
            return 0
        if not settings.apify_token:
            print("Set APIFY_TOKEN in .env")
            return 1
        out_dir = settings.database_path.parent / "apify" / "experiments" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        out_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for sort, date_range in args.config:
            payload = actor_input(args.term, results_per_term=args.sample_size, sort=sort, date_range=date_range)
            run_items, _ = run_actor(
                settings.apify_token, payload, actor=ACTOR, max_items=per_run, max_charge_usd=args.max_charge_usd
            )
            items += [{**item, "_config": f"{sort}:{date_range}"} for item in run_items]
            (out_dir / "items.json").write_text(json.dumps(items))
        print(f"Saved {len(items)} Apify results to {out_dir / 'items.json'}")

    samples: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in items:
        samples[(item["_config"], str(item.get("searchQuery") or ""))].append(item)

    repository = LeadRepository(settings.database_path)
    repository.initialize()
    try:
        known = repository.seen_handles()
    finally:
        repository.close()

    candidates = {
        key: pick_candidates(
            sample,
            term_niches={},
            default_niche=args.niche,
            known=known,
            min_followers=args.min_followers,
            max_followers=args.max_followers,
            min_video_views=args.min_video_views,
        )
        for key, sample in samples.items()
    }

    profiles = None
    if not args.no_verify:
        handles = sorted({c.handle for picked in candidates.values() for c in picked})
        print(f"Looking up {len(handles)} candidate profiles (read-only)")
        profiles = asyncio.run(look_up(settings, handles, args.niche))

    report = write_report(out_dir, samples, candidates, profiles, known, settings)
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
