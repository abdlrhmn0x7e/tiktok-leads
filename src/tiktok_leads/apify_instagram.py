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
import csv
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tiktok_leads.apify_client import run_actor
from tiktok_leads.bio_links import emails_from_bio_links
from tiktok_leads.db import LeadRepository
from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.models import Lead
from tiktok_leads.niches import is_on_niche, rejected_group
from tiktok_leads.notifiers import build_notifier
from tiktok_leads.numbers import floor_view_count, robust_average_view_count
from tiktok_leads.settings import Settings

logger = logging.getLogger(__name__)

SEARCH_ACTOR = "apify~instagram-hashtag-scraper"
PROFILE_ACTOR = "apify~instagram-profile-scraper"
REEL_ACTOR = "apify~instagram-reel-scraper"
SOURCE_LABEL = "apify"
# shortcut: the free-plan price every actor here charges per result; paid tiers are cheaper, so this overestimates there.
SEARCH_RESULT_USD = 0.0026
HANDLE_BATCH = 50


@dataclass(frozen=True)
class ReelHit:
    handle: str
    niche: str
    found_via: str
    views: int
    # An email from a handle list: the creator becomes a lead whatever their numbers.
    email: str | None = None


def search_input(terms: list[str], *, results_per_term: int) -> dict:
    return {"hashtags": terms, "keywordSearch": True, "resultsType": "reels", "resultsLimit": results_per_term}


def clean_handle(token: str) -> str | None:
    """A handle pasted from anywhere: strips @, stray punctuation around it and +N
    suffixes. None when what's left still isn't a valid TikTok/Instagram username."""
    raw = token.strip().lower().lstrip("@,:; ").split("+")[0].replace(",", ".")
    match = re.match(r"[a-z0-9._]+", raw)
    if not match or any(ch.isalnum() for ch in raw[match.end() :]):
        return None
    handle = match.group().strip(".")
    return handle if 0 < len(handle) <= 30 else None


def read_handles(path: Path) -> list[str]:
    handles = (clean_handle(token) for token in path.read_text().split())
    return list(dict.fromkeys(handle for handle in handles if handle))


def search_cursor(term: str) -> str:
    return f"instagram-search:{term}"


def next_search(depths: dict[str, int], step: int) -> tuple[list[str], int]:
    """The actor has no offset, so going past the results a term already returned means
    asking for more and paying for the repeats. One run takes one resultsLimit, so only
    the shallowest terms go deeper each run."""
    shallowest = min(depths.values())
    return [term for term, depth in depths.items() if depth == shallowest], shallowest + step


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
            best[handle] = ReelHit(handle, term_niches.get(term, default_niche), f"search:{term}", views)
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


def reel_views_and_captions(reels: list[dict]) -> tuple[dict[str, list[int]], dict[str, list[str]]]:
    views: dict[str, list[int]] = {}
    captions: dict[str, list[str]] = {}
    for reel in reels:
        owner = str(reel.get("ownerUsername") or "").lower()
        if not owner:
            continue
        count = reel.get("videoPlayCount") or reel.get("videoViewCount")
        if count:
            views.setdefault(owner, []).append(int(count))
        cap = str(reel.get("caption") or "").strip()
        if cap:
            captions.setdefault(owner, []).append(cap)
    return views, captions


def reel_views(reels: list[dict]) -> dict[str, list[int]]:
    return reel_views_and_captions(reels)[0]


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
    if not settings.apify_token and not (args.dry_run or (args.items_file and args.discover_only)):
        print("Set APIFY_TOKEN in .env")
        return 1
    budget = Budget(settings.apify_token or "", args.max_charge_usd)
    repository = LeadRepository(settings.database_path)
    repository.initialize()
    try:
        if args.handles_file:
            return run_handles(settings, repository, budget, args)
        if args.items_file and not args.dry_run:
            items = json.loads(args.items_file.read_text())
        else:
            depths = {
                term: repository.get_feed_cursor(search_cursor(term), max_age_days=settings.feed_cursor_max_age_days)
                for term in term_niches
            }
            terms, depth = next_search(depths, args.results_per_term or 10)
            # Half the budget goes to the search so profiles and reels can still run; the
            # terms left out are still the shallowest next run, so they rotate in.
            terms = terms[: max(1, int(args.max_charge_usd / 2 / (depth * SEARCH_RESULT_USD)))]
            print(
                f"Searching {len(terms)} of {len(depths)} terms {depth} reels deep "
                f"(up to ${depth * len(terms) * SEARCH_RESULT_USD:.2f} of ${args.max_charge_usd:.2f})"
            )
            if args.dry_run:
                print(json.dumps(search_input(terms, results_per_term=depth), indent=2))
                return 0
            items = budget.run(SEARCH_ACTOR, search_input(terms, results_per_term=depth), max_items=len(terms) * depth)
            out = settings.database_path.parent / "apify" / f"instagram-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(items))
            print(f"Saved {len(items)} Instagram reels to {out}")
            reels = Counter(term_of(item) for item in items if item.get("id"))
            capped = budget.remaining <= 0.01
            if capped:
                print("Search stopped at --max-charge-usd; terms it didn't finish stay at their old depth")
            for term in terms:
                # Short of depth means the term ran out of reels, unless the budget cut the run off.
                if reels[term] >= depth or not capped:
                    repository.set_feed_cursor(search_cursor(term), depth)
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


def run_handles(settings: Settings, repository: LeadRepository, budget: Budget, args: argparse.Namespace) -> int:
    skip = repository.seen_instagram_handles()
    hits = []
    for path in args.handles_file:
        if path.suffix == ".csv":
            with path.open(newline="") as file:
                listed = [
                    ReelHit(row["instagram"], args.niche, f"handles:{row['source']}", 0, row["email"] or None)
                    for row in csv.DictReader(file)
                ]
        else:
            listed = [ReelHit(handle, args.niche, f"handles:{path.stem}", 0) for handle in read_handles(path)]
        for hit in listed:
            if hit.handle not in skip:
                skip.add(hit.handle)
                hits.append(hit)
    print(f"{len(hits)} handles not checked yet; up to ${len(hits) * SEARCH_RESULT_USD:.2f} for all their profiles")
    if args.dry_run or not hits:
        return 0
    inserted = 0
    for start in range(0, len(hits), HANDLE_BATCH):
        if budget.remaining <= 0.01:
            print(f"Budget spent after {start} handles; run the same command again to continue")
            break
        inserted += verify_instagram(settings, repository, budget, hits[start : start + HANDLE_BATCH], args)
    print(f"Inserted {inserted} new Instagram lead(s).")
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
            handle=hit.handle, niche=hit.niche, skip_reason=skip, found_via=hit.found_via
        )

    profiles = budget.run(PROFILE_ACTOR, {"usernames": [hit.handle for hit in hits]}, max_items=len(hits))
    by_handle = {str(p.get("username") or "").lower(): p for p in profiles}

    emailed: list[tuple[ReelHit, dict, str]] = []
    for hit in hits:
        profile = by_handle.get(hit.handle)
        if profile is None:
            continue  # not recorded: a later run retries it
        if profile.get("error"):  # e.g. not_found: deleted or renamed, so never pay for it again
            record(hit, str(profile["error"]))
            continue
        followers = int(profile.get("followersCount") or 0)
        if profile.get("private"):
            record(hit, "private")
            continue
        if not hit.email and not args.min_followers <= followers <= args.max_followers:
            record(hit, "low_followers" if followers < args.min_followers else "high_followers")
            continue
        emails = [hit.email] if hit.email else profile_emails(profile, resolve_links=settings.resolve_bio_link_emails)
        if not emails:
            record(hit, "no_email")
            continue
        if repository.exists_by_email(emails[0]):
            record(hit, "known_email")
            continue
        bio = str(profile.get("biography") or "")
        if not hit.email and rejected_group(hit.niche, bio, [], emails[0]) is not None:
            record(hit, "off_niche")
            continue
        emailed.append((hit, profile, emails[0]))
    print(f"{len(emailed)} creators in the follower band with a new email")
    if not emailed:
        return 0
    if budget.remaining <= 0.01:
        return 0  # left unrecorded, so a later run checks them again

    reels = budget.run(
        REEL_ACTOR,
        {
            "username": [hit.handle for hit, _, _ in emailed],
            "resultsLimit": args.reels_per_creator,
            "skipPinnedPosts": True,
        },
        max_items=len(emailed) * args.reels_per_creator,
    )
    views, captions_by_handle = reel_views_and_captions(reels)
    capped = budget.remaining <= 0.01

    notifier = build_notifier(settings)
    inserted = 0
    for hit, profile, email in emailed:
        if capped and hit.handle not in views:
            continue  # the budget cut the reel run off before this creator; retried next run
        average = robust_average_view_count(views.get(hit.handle, []))
        if not hit.email and (average is None or average < settings.min_average_views):
            record(hit, "low_views")
            continue
        bio = str(profile.get("biography") or "")
        caps = captions_by_handle.get(hit.handle, [])
        if not hit.email and (
            not is_on_niche(hit.niche, bio, caps) or rejected_group(hit.niche, bio, caps, email) is not None
        ):
            record(hit, "off_niche")
            continue
        record(hit, None)
        lead = Lead(
            handle=hit.handle,
            profile_url=f"https://www.instagram.com/{hit.handle}/",
            niche=hit.niche,
            email=email,
            followers_count=int(profile.get("followersCount") or 0),
            average_views=floor_view_count(average) if average else 0,
            source=SOURCE_LABEL,
            found_via=hit.found_via,
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
            repository.mark_notified(lead.email, lead.platform)
    return inserted
