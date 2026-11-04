"""Find new creators with Apify's TikTok video search, then verify them with the scraper.

Apify runs the leads-hub search terms as video searches. Its results carry each
creator's follower count and the matching video's views, but no bio, so every
new creator that passes those cheap filters goes through the regular scraper
(profile, bio-link emails, recent-video average) and becomes a lead the same
way a daemon find does.

    uv run tiktok-leads-apify --dry-run          # show the Apify input and cost cap
    uv run tiktok-leads-apify                     # discover with Apify, then verify
    uv run tiktok-leads-apify --discover-only     # only spend Apify budget, save results
    uv run tiktok-leads-apify --items-file data/apify/search-....json   # verify saved results
    uv run tiktok-leads-apify --platform instagram   # Instagram, all stages via Apify (apify_instagram.py)

Already-evaluated creators are skipped, so re-running with --items-file after a
TikTok block continues where it stopped without paying Apify again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tiktok_leads.apify_client import run_actor
from tiktok_leads.apify_instagram import run_instagram
from tiktok_leads.cli import configure_logging
from tiktok_leads.daemon import fetch_search_terms
from tiktok_leads.db import LeadRepository
from tiktok_leads.factory import build_source
from tiktok_leads.notifiers import build_notifier
from tiktok_leads.runner import scrape_handles
from tiktok_leads.settings import Settings
from tiktok_leads.sources.tiktokapi_source import TikTokBlockedError

logger = logging.getLogger(__name__)

ACTOR = "clockworks~tiktok-scraper"
SORTS = {"most-liked": "MOST_LIKED", "most-relevant": "MOST_RELEVANT", "latest": "LATEST"}
DATE_RANGES = {
    "all-time": "ALL_TIME",
    "past-day": "PAST_24_HOURS",
    "past-week": "PAST_WEEK",
    "past-month": "PAST_MONTH",
    "past-3-months": "LAST_3_MONTHS",
    "past-6-months": "LAST_6_MONTHS",
}
SOURCE_LABEL = "apify"


@dataclass(frozen=True)
class Candidate:
    handle: str
    niche: str
    term: str
    followers: int
    top_video_views: int


def actor_input(terms: list[str], *, results_per_term: int, sort: str, date_range: str) -> dict:
    return {
        "searchQueries": terms,
        "searchSection": "/video",
        "resultsPerPage": results_per_term,
        "videoSearchSorting": SORTS[sort],
        "videoSearchDateFilter": DATE_RANGES[date_range],
    }


def pick_candidates(
    items: list[dict],
    *,
    term_niches: dict[str, str],
    default_niche: str,
    known: set[str],
    min_followers: int,
    max_followers: int,
    min_video_views: int,
) -> list[Candidate]:
    """New, public creators inside the follower band whose matching video
    cleared the views bar, one per creator, keeping their best video."""
    best: dict[str, Candidate] = {}
    for item in items:
        author = item.get("authorMeta") or {}
        handle = str(author.get("name") or "").removeprefix("@").strip().lower()
        followers = author.get("fans") or 0
        views = item.get("playCount") or 0
        if (
            not handle
            or handle in known
            or author.get("privateAccount")
            or not min_followers <= followers <= max_followers
            or views < min_video_views
        ):
            continue
        term = str(item.get("searchQuery") or "")
        if handle not in best or views > best[handle].top_video_views:
            best[handle] = Candidate(
                handle=handle,
                niche=term_niches.get(term, default_niche),
                term=term,
                followers=followers,
                top_video_views=views,
            )
    return sorted(best.values(), key=lambda c: -c.top_video_views)


async def verify(settings: Settings, repository: LeadRepository, candidates: list[Candidate]) -> int:
    by_term: dict[tuple[str, str], list[str]] = {}
    for candidate in candidates:
        by_term.setdefault((candidate.term, candidate.niche), []).append(candidate.handle)
    notifier = build_notifier(settings)
    inserted = 0
    async with build_source(settings) as source:
        for (term, niche), handles in by_term.items():
            inserted += await scrape_handles(
                source,
                repository,
                notifier,
                handles=handles,
                niche=niche,
                min_followers=settings.min_followers,
                min_average_views=settings.min_average_views,
                found_via=f"search:{term}",
                source_label=SOURCE_LABEL,
            )
    return inserted


def parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--term",
        action="append",
        default=[],
        help="Search term to run (repeatable). Default: every search term in leads-hub",
    )
    parser.add_argument("--niche", default="fitness", help="Niche for --term terms (default: fitness)")
    parser.add_argument(
        "--platform",
        choices=["tiktok", "instagram"],
        default="tiktok",
        help="tiktok: video search, verified free by the scraper. instagram: reel search, profiles and "
        "recent reels all through Apify (default: tiktok)",
    )
    parser.add_argument(
        "--results-per-term",
        type=int,
        help="Apify search results per term (default: 30 for tiktok, 10 for instagram, which also pays "
        "for each creator's profile and reels)",
    )
    parser.add_argument(
        "--reels-per-creator",
        type=int,
        default=6,
        help="Instagram only: recent reels fetched to average a creator's views (default: 6)",
    )
    parser.add_argument(
        "--sort",
        choices=SORTS,
        default="most-liked",
        help="TikTok only. Video search order (default: most-liked; a 2026-11 sample found it gave 3x the usable "
        "new creators of latest)",
    )
    parser.add_argument(
        "--date-range",
        choices=DATE_RANGES,
        default="all-time",
        help="TikTok only. Video upload window (default: all-time)",
    )
    parser.add_argument(
        "--max-charge-usd",
        type=float,
        default=4.5,
        help="Hard cap on Apify spend for the whole run (default: 4.5; the free plan allows $5/month)",
    )
    add_filter_args(parser, settings)
    parser.add_argument("--items-file", type=Path, help="Verify saved Apify results instead of running Apify")
    parser.add_argument("--discover-only", action="store_true", help="Run Apify and save results, skip verification")
    parser.add_argument("--dry-run", action="store_true", help="Print the Apify input and exit without spending")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    return parser.parse_args()


def add_filter_args(parser: argparse.ArgumentParser, settings: Settings) -> None:
    parser.add_argument(
        "--min-video-views",
        type=int,
        default=15_000,
        help="Views the matching video needs, a margin over MIN_AVERAGE_VIEWS (default: 15000)",
    )
    parser.add_argument(
        "--min-followers",
        type=int,
        default=settings.min_followers,
        help=f"Follower floor (default: MIN_FOLLOWERS={settings.min_followers})",
    )
    parser.add_argument(
        "--max-followers",
        type=int,
        default=250_000,
        help="Follower ceiling; bigger creators are usually already pitched (default: 250000)",
    )


def main() -> int:
    settings = Settings()
    args = parse_args(settings)
    configure_logging(args.log_level)
    if settings.tiktok_suppress_library_errors:
        logging.getLogger("TikTokApi.tiktok").setLevel(logging.CRITICAL)

    term_niches = {term: args.niche for term in args.term} or dict(fetch_search_terms(settings))
    if not term_niches and not args.items_file:
        print("No search terms: pass --term or set CONVEX_SITE_URL and CONVEX_INGEST_API_KEY.")
        return 1
    if args.platform == "instagram":
        return run_instagram(settings, args, term_niches)
    args.results_per_term = args.results_per_term or 30

    if args.items_file:
        items = json.loads(args.items_file.read_text())
    else:
        terms = list(term_niches)
        payload = actor_input(
            terms, results_per_term=args.results_per_term, sort=args.sort, date_range=args.date_range
        )
        max_items = len(terms) * args.results_per_term
        if args.dry_run:
            print(json.dumps(payload, indent=2))
            print(f"{len(terms)} terms, up to {max_items} results, capped at ${args.max_charge_usd:.2f}")
            return 0
        if not settings.apify_token:
            print("Set APIFY_TOKEN in .env")
            return 1
        items, cost = run_actor(
            settings.apify_token, payload, actor=ACTOR, max_items=max_items, max_charge_usd=args.max_charge_usd
        )
        logger.info("apify run cost $%.2f", cost)
        out = settings.database_path.parent / "apify" / f"search-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(items))
        print(f"Saved {len(items)} Apify results to {out}")

    repository = LeadRepository(settings.database_path)
    repository.initialize()
    try:
        candidates = pick_candidates(
            items,
            term_niches=term_niches,
            default_niche=args.niche,
            known=repository.seen_handles(),
            min_followers=args.min_followers,
            max_followers=args.max_followers,
            min_video_views=args.min_video_views,
        )
        print(f"{len(items)} Apify results, {len(candidates)} new candidates to verify")
        if args.discover_only or not candidates:
            return 0
        try:
            inserted = asyncio.run(verify(settings, repository, candidates))
        except TikTokBlockedError as error:
            print(f"TikTok is blocking requests ({error}). Re-run with --items-file to continue later.")
            return 2
    finally:
        repository.close()
    print(f"Inserted {inserted} new lead(s) from {len(candidates)} candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
