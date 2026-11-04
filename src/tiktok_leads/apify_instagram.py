"""Instagram leads through Apify: keyword reel search, then profile, then recent reels.

Each stage only pays for creators that survived the one before:
1. Reel keyword search per term. Keeps creators not evaluated before whose
   matching reel cleared --min-video-views.
2. Profile lookup. Keeps creators inside the follower band with an email in
   their bio or bio link that isn't already a lead.
3. Recent reels. Keeps creators whose robust average clears MIN_AVERAGE_VIEWS.

Every looked-up creator is recorded in instagram_profiles so later runs skip it.
Leads go to SQLite and Convex with platform=instagram.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from tiktok_leads.apify_client import run_actor
from tiktok_leads.bio_links import emails_from_bio_links
from tiktok_leads.db import LeadRepository
from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.models import Lead
from tiktok_leads.notifiers import build_notifier
from tiktok_leads.numbers import floor_view_count, robust_average_view_count
from tiktok_leads.settings import Settings

logger = logging.getLogger(__name__)

SEARCH_ACTOR = "apify~instagram-hashtag-scraper"
PROFILE_ACTOR = "apify~instagram-profile-scraper"
REEL_ACTOR = "apify~instagram-reel-scraper"
SOURCE_LABEL = "apify"


@dataclass(frozen=True)
class ReelHit:
    handle: str
    niche: str
    term: str
    views: int


def search_input(terms: list[str], *, results_per_term: int) -> dict:
    return {"hashtags": terms, "keywordSearch": True, "resultsType": "reels", "resultsLimit": results_per_term}


def term_of(item: dict) -> str:
    """Keyword searches only echo the search URL, e.g. .../keyword/?q=high%20protein."""
    query = parse_qs(urlsplit(str(item.get("inputUrl") or "")).query)
    return (query.get("q") or [""])[0]


def pick_reel_hits(
    items: list[dict],
    *,
    term_niches: dict[str, str],
    default_niche: str,
    known: set[str],
    min_video_views: int,
) -> list[ReelHit]:
    best: dict[str, ReelHit] = {}
    for item in items:
        handle = str(item.get("ownerUsername") or "").strip().lower()
        views = item.get("videoPlayCount") or item.get("videoViewCount") or 0
        # Items flagged "restricted_page" still carry usable partial data.
        if not handle or handle in known or views < min_video_views:
            continue
        if handle not in best or views > best[handle].views:
            term = term_of(item)
            best[handle] = ReelHit(handle, term_niches.get(term, default_niche), term, views)
    return sorted(best.values(), key=lambda hit: -hit.views)


def profile_emails(profile: dict, *, resolve_links: bool) -> list[str]:
    bio = str(profile.get("biography") or "")
    links = [str(profile.get("externalUrl") or "")]
    links += [str(link.get("url") or "") for link in profile.get("externalUrls") or []]
    links = [link for link in links if link]
    emails = extract_emails(bio, *links)
    if not emails and resolve_links and links:
        emails = emails_from_bio_links(bio, links)
    return emails


def reel_views(reels: list[dict]) -> dict[str, list[int]]:
    views: dict[str, list[int]] = {}
    for reel in reels:
        count = reel.get("videoPlayCount") or reel.get("videoViewCount")
        if count:
            views.setdefault(str(reel.get("ownerUsername") or "").lower(), []).append(int(count))
    return views


class Budget:
    """One spend cap shared by the three actor runs."""

    def __init__(self, token: str, max_usd: float) -> None:
        self.token = token
        self.remaining = max_usd

    def run(self, actor: str, payload: dict, *, max_items: int) -> list[dict]:
        if self.remaining <= 0.01:
            raise RuntimeError("Apify budget for this run is spent (raise --max-charge-usd)")
        items, cost = run_actor(
            self.token, payload, actor=actor, max_items=max_items, max_charge_usd=self.remaining
        )
        self.remaining -= cost
        logger.info("%s: %s items for $%.2f, $%.2f left", actor, len(items), cost, self.remaining)
        return items


def run_instagram(settings: Settings, args: argparse.Namespace, term_niches: dict[str, str]) -> int:
    results_per_term = args.results_per_term or 10
    terms = list(term_niches)
    if args.dry_run:
        print(json.dumps(search_input(terms, results_per_term=results_per_term), indent=2))
        print(
            f"{len(terms)} terms, up to {len(terms) * results_per_term} reels, then profiles and "
            f"{args.reels_per_creator} reels per surviving creator, all capped at ${args.max_charge_usd:.2f}"
        )
        return 0
    if not settings.apify_token and not (args.items_file and args.discover_only):
        print("Set APIFY_TOKEN in .env")
        return 1
    budget = Budget(settings.apify_token or "", args.max_charge_usd)

    if args.items_file:
        items = json.loads(args.items_file.read_text())
    else:
        items = budget.run(
            SEARCH_ACTOR, search_input(terms, results_per_term=results_per_term), max_items=len(terms) * results_per_term
        )
        out = settings.database_path.parent / "apify" / f"instagram-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(items))
        print(f"Saved {len(items)} Instagram reels to {out}")

    repository = LeadRepository(settings.database_path)
    repository.initialize()
    try:
        hits = pick_reel_hits(
            items,
            term_niches=term_niches,
            default_niche=args.niche,
            known=repository.seen_instagram_handles(),
            min_video_views=args.min_video_views,
        )
        print(f"{len(items)} reels, {len(hits)} new creators with a reel >= {args.min_video_views:,} views")
        if args.discover_only or not hits:
            return 0
        inserted = verify_instagram(settings, repository, budget, hits, args)
    finally:
        repository.close()
    print(f"Inserted {inserted} new Instagram lead(s) from {len(hits)} creators.")
    return 0


def verify_instagram(
    settings: Settings,
    repository: LeadRepository,
    budget: Budget,
    hits: list[ReelHit],
    args: argparse.Namespace,
) -> int:
    def record(hit: ReelHit, skip: str | None) -> None:
        repository.record_instagram_profile(
            handle=hit.handle, niche=hit.niche, skip_reason=skip, found_via=f"search:{hit.term}"
        )

    profiles = budget.run(PROFILE_ACTOR, {"usernames": [hit.handle for hit in hits]}, max_items=len(hits))
    by_handle = {str(p.get("username") or "").lower(): p for p in profiles}

    emailed: list[tuple[ReelHit, dict, str]] = []
    for hit in hits:
        profile = by_handle.get(hit.handle)
        if profile is None:
            continue  # not recorded: a later run retries it
        followers = int(profile.get("followersCount") or 0)
        if profile.get("private"):
            record(hit, "private")
            continue
        if not args.min_followers <= followers <= args.max_followers:
            record(hit, "low_followers" if followers < args.min_followers else "high_followers")
            continue
        emails = profile_emails(profile, resolve_links=settings.resolve_bio_link_emails)
        if not emails:
            record(hit, "no_email")
            continue
        if repository.exists_by_email(emails[0]):
            record(hit, "known_email")
            continue
        emailed.append((hit, profile, emails[0]))
    print(f"{len(emailed)} creators in the follower band with a new email")
    if not emailed:
        return 0

    reels = budget.run(
        REEL_ACTOR,
        {
            "username": [hit.handle for hit, _, _ in emailed],
            "resultsLimit": args.reels_per_creator,
            "skipPinnedPosts": True,
        },
        max_items=len(emailed) * args.reels_per_creator,
    )
    views = reel_views(reels)

    notifier = build_notifier(settings)
    inserted = 0
    for hit, profile, email in emailed:
        average = robust_average_view_count(views.get(hit.handle, []))
        if average is None or average < settings.min_average_views:
            record(hit, "low_views")
            continue
        record(hit, None)
        lead = Lead(
            handle=hit.handle,
            profile_url=f"https://www.instagram.com/{hit.handle}/",
            niche=hit.niche,
            email=email,
            followers_count=int(profile.get("followersCount") or 0),
            average_views=floor_view_count(average),
            source=SOURCE_LABEL,
            found_via=f"search:{hit.term}",
            platform="instagram",
        )
        if not repository.insert_lead(lead):
            continue
        inserted += 1
        logger.info("inserted instagram @%s email=%s average_views=%s", lead.handle, email, average)
        try:
            notifier.send(lead)
        except Exception:
            logger.exception("failed to send @%s; the daemon's retry will push it", lead.handle)
        else:
            repository.mark_notified(lead.email)
    return inserted
