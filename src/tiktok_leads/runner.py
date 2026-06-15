from __future__ import annotations

import logging
from collections.abc import AsyncIterable, Iterable, Sized

from tiktok_leads.db import LeadRepository
from tiktok_leads.filters import evaluate_candidate
from tiktok_leads.models import CandidateProfile
from tiktok_leads.numbers import floor_view_count
from tiktok_leads.notifiers import Notifier
from tiktok_leads.sources.base import TikTokSource

logger = logging.getLogger(__name__)


async def scrape_handles(
    source: TikTokSource,
    repository: LeadRepository,
    notifier: Notifier,
    *,
    handles: Iterable[str],
    niche: str,
    min_followers: int,
    min_average_views: int,
    found_via: str = "handle",
) -> int:
    handle_count = len(handles) if isinstance(handles, Sized) else "provided"
    logger.info("checking %s handle(s) for niche=%s", handle_count, niche)
    return await _process_candidates(
        source.profiles_from_handles(handles, niche=niche),
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        found_via=found_via,
    )


async def scrape_hashtag(
    source: TikTokSource,
    repository: LeadRepository,
    notifier: Notifier,
    *,
    hashtag: str,
    niche: str,
    limit: int,
    min_followers: int,
    min_average_views: int,
    exclude_handles: set[str] | None = None,
    feed_cursor_max_age_days: int = 14,
) -> int:
    tag = hashtag.removeprefix("#").strip().lower()
    start_cursor = 0
    on_cursor = None
    if feed_cursor_max_age_days > 0:
        feed_key = f"hashtag:{tag}"
        start_cursor = repository.get_feed_cursor(feed_key, max_age_days=feed_cursor_max_age_days)
        on_cursor = lambda cursor: repository.set_feed_cursor(feed_key, cursor)  # noqa: E731
    logger.info(
        "crawling hashtag #%s for niche=%s limit=%s cursor=%s", tag, niche, limit, start_cursor
    )
    return await _process_candidates(
        source.profiles_from_hashtag(
            hashtag,
            niche=niche,
            limit=limit,
            exclude_handles=exclude_handles,
            start_cursor=start_cursor,
            on_cursor=on_cursor,
        ),
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        found_via=f"#{tag}",
    )


async def scrape_search(
    source: TikTokSource,
    repository: LeadRepository,
    notifier: Notifier,
    *,
    query: str,
    niche: str,
    limit: int,
    min_followers: int,
    min_average_views: int,
    exclude_handles: set[str] | None = None,
) -> int:
    logger.info("searching users for '%s' (niche=%s limit=%s)", query, niche, limit)
    return await _process_candidates(
        source.profiles_from_search(
            query,
            niche=niche,
            limit=limit,
            exclude_handles=exclude_handles,
        ),
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        found_via=f"search:{query}",
    )


async def _process_candidates(
    candidates: AsyncIterable[CandidateProfile],
    repository: LeadRepository,
    notifier: Notifier,
    *,
    min_followers: int,
    min_average_views: int,
    found_via: str | None = None,
) -> int:
    inserted = 0
    async for candidate in candidates:
        average_views = candidate.average_views
        display_average_views = (
            floor_view_count(average_views) if average_views is not None else "unknown"
        )
        logger.info(
            "checking @%s followers=%s average_views=%s source=%s",
            candidate.handle or "unknown",
            candidate.followers_count if candidate.followers_count is not None else "unknown",
            display_average_views,
            candidate.source,
        )

        evaluation = evaluate_candidate(
            candidate,
            min_followers=min_followers,
            min_average_views=min_average_views,
        )
        repository.record_scraped_profile(
            handle=candidate.handle,
            niche=candidate.niche,
            source=candidate.source,
            skip_reason=evaluation.skip_code,
            found_via=found_via,
        )
        # Hashtags from creators below the follower bar are noise-heavy; only
        # harvest from accounts that at least qualified on followers.
        if candidate.discovered_hashtags and evaluation.skip_code != "low_followers":
            repository.record_discovered_hashtags(candidate.discovered_hashtags, niche=candidate.niche)

        if evaluation.lead is None:
            logger.info("skipped @%s: %s", candidate.handle or "unknown", evaluation.skip_reason)
            continue

        lead = evaluation.lead
        if repository.exists_by_email(lead.email):
            logger.info("skipped @%s: email already exists (%s)", lead.handle, lead.email)
            continue

        if repository.insert_lead(lead):
            logger.info("inserted @%s email=%s", lead.handle, lead.email)
            logger.info("sending notification for @%s", lead.handle)
            try:
                notifier.send(lead)
            except Exception:
                logger.exception("failed to send notification for @%s", lead.handle)
            else:
                repository.mark_notified(lead.email)
                logger.info("notification marked sent for @%s", lead.handle)
            inserted += 1
    return inserted
