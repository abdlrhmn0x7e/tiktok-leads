from __future__ import annotations

import logging
from collections.abc import AsyncIterable, Callable, Iterable, Mapping, Sized
from dataclasses import replace

from tiktok_leads.db import LeadRepository
from tiktok_leads.filters import evaluate_candidate
from tiktok_leads.models import CandidateProfile
from tiktok_leads.numbers import floor_view_count
from tiktok_leads.notifiers import Notifier
from tiktok_leads.sources.base import TikTokSource

logger = logging.getLogger(__name__)

# User-search always starts at cursor 0, and a hashtag feed serves only a few
# hundred videos before wrapping to its head, so a crawl that surfaced no new
# creators would return the same evaluated users again; it rests this long.
EXHAUSTED_DAYS = {"search": 7, "hashtag": 2}


async def scrape_handles(
    source: TikTokSource,
    repository: LeadRepository,
    notifier: Notifier,
    *,
    handles: Iterable[str],
    niche: str,
    min_followers: int,
    min_average_views: int,
    max_followers: int | None = None,
    found_via: str | None = "handle",
    found_via_by_handle: Mapping[str, str | None] | None = None,
    mark_bio_link_enriched: bool = False,
    source_label: str | None = None,
    known_emails: Mapping[str, str] | None = None,
    exclude_handles: set[str] | None = None,
) -> int:
    """`known_emails` maps handle -> an email found off-profile (e.g. in a
    search snippet), used when the bio has none."""
    handle_count = len(handles) if isinstance(handles, Sized) else "provided"
    logger.info("checking %s handle(s) for niche=%s", handle_count, niche)
    candidates = source.profiles_from_handles(handles, niche=niche)
    if source_label:
        candidates = (replace(candidate, source=source_label) async for candidate in candidates)
    if known_emails:
        candidates = (
            replace(candidate, extra_emails=[*candidate.extra_emails, known_emails[candidate.handle.lower()]])
            if candidate.handle.lower() in known_emails
            else candidate
            async for candidate in candidates
        )
    return await _process_candidates(
        candidates,
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        max_followers=max_followers,
        found_via=found_via,
        found_via_by_handle=found_via_by_handle,
        mark_bio_link_enriched=mark_bio_link_enriched,
        exclude_handles=exclude_handles,
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
    max_followers: int | None = None,
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
            on_known=lambda handle: _add_lead_source(repository, notifier, handle, f"#{tag}"),
            on_finished=_mark_exhausted(repository, "hashtag", tag),
        ),
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        max_followers=max_followers,
        found_via=f"#{tag}",
        exclude_handles=exclude_handles,
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
    max_followers: int | None = None,
    exclude_handles: set[str] | None = None,
) -> int:
    logger.info("searching users for '%s' (niche=%s limit=%s)", query, niche, limit)
    return await _process_candidates(
        source.profiles_from_search(
            query,
            niche=niche,
            limit=limit,
            exclude_handles=exclude_handles,
            on_known=lambda handle: _add_lead_source(
                repository, notifier, handle, f"search:{query}", niche=niche
            ),
            on_finished=_mark_exhausted(repository, "search", query),
        ),
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        max_followers=max_followers,
        found_via=f"search:{query}",
        exclude_handles=exclude_handles,
    )


async def scrape_following(
    source: TikTokSource,
    repository: LeadRepository,
    notifier: Notifier,
    *,
    seed: str,
    max_pages: int,
    min_followers: int,
    min_average_views: int,
    max_followers: int | None = None,
    exclude_handles: set[str] | None = None,
) -> int:
    current = repository.following_seed(seed)
    # A blocked cycle re-queues its whole batch, so a seed finished earlier in it comes back.
    if current is None or current.state != "due":
        return 0
    found_via = f"following:@{seed}"
    logger.info(
        "crawling who @%s follows for niche=%s level=%s cursor=%s",
        seed,
        current.niche,
        current.level,
        current.cursor,
    )
    # Cap following-graph expansion at depth 3 (level 1 -> 2 -> 3), gated by
    # strict gym-bio + English + follower-range checks on every promoted seed.
    next_level = current.level + 1
    on_seed = (
        (lambda handle, sec_uid: repository.add_found_seed(handle, current.niche, sec_uid, level=next_level))
        if current.level <= 2
        else None
    )
    return await _process_candidates(
        source.profiles_from_following(
            seed,
            niche=current.niche,
            sec_uid=current.sec_uid,
            cursor=current.cursor,
            max_pages=max_pages,
            on_progress=lambda sec_uid, cursor, state: repository.save_following_progress(
                seed, sec_uid=sec_uid, cursor=cursor, state=state
            ),
            # No on_known: popular creators are followed by many seeds, and each would add a source to the lead.
            exclude_handles=exclude_handles,
            on_seed=on_seed,
        ),
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        max_followers=max_followers,
        found_via=found_via,
        exclude_handles=exclude_handles,
    )


async def scrape_related(
    source: TikTokSource,
    repository: LeadRepository,
    notifier: Notifier,
    *,
    seed: str,
    niche: str,
    max_videos: int,
    min_followers: int,
    min_average_views: int,
    max_followers: int | None = None,
    exclude_handles: set[str] | None = None,
) -> int:
    found_via = f"related:@{seed}"
    logger.info("crawling related videos for @%s (niche=%s max_videos=%s)", seed, niche, max_videos)
    inserted = await _process_candidates(
        source.profiles_from_related(
            seed,
            niche=niche,
            max_videos=max_videos,
            exclude_handles=exclude_handles,
            on_seed=lambda handle, sec_uid: repository.add_found_seed(handle, niche, sec_uid, level=2),
        ),
        repository,
        notifier,
        min_followers=min_followers,
        min_average_views=min_average_views,
        max_followers=max_followers,
        found_via=found_via,
        exclude_handles=exclude_handles,
    )
    repository.mark_related_crawled(seed)
    return inserted


def is_exhausted(repository: LeadRepository, kind: str, value: str) -> bool:
    return (
        repository.get_feed_cursor(_exhausted_key(kind, value), max_age_days=EXHAUSTED_DAYS[kind])
        == 1
    )


def _mark_exhausted(repository: LeadRepository, kind: str, value: str) -> Callable[[int], None]:
    return lambda new_creators: repository.set_feed_cursor(
        _exhausted_key(kind, value), 0 if new_creators else 1
    )


def _exhausted_key(kind: str, value: str) -> str:
    return f"{kind}-exhausted:{value.strip().lower()}"


def _add_lead_source(
    repository: LeadRepository,
    notifier: Notifier,
    handle: str,
    found_via: str,
    *,
    niche: str | None = None,
) -> None:
    """An already-evaluated creator turned up again. If they're a lead and
    this source is new for them (or `niche` corrects theirs), push it to
    Convex; a failed push stays queued for the daemon's retry."""
    lead = repository.add_lead_source(handle, found_via, niche=niche)
    if lead is None:
        return
    try:
        notifier.send_found_via(lead)
    except Exception:
        logger.exception("failed to send source %s for @%s", found_via, lead.handle)
        return
    repository.mark_lead_source_notified(lead.email, found_via)
    logger.info("added source %s to @%s (niche=%s)", found_via, lead.handle, lead.niche)


async def _process_candidates(
    candidates: AsyncIterable[CandidateProfile],
    repository: LeadRepository,
    notifier: Notifier,
    *,
    min_followers: int,
    min_average_views: int,
    max_followers: int | None = None,
    found_via: str | None = None,
    found_via_by_handle: Mapping[str, str | None] | None = None,
    mark_bio_link_enriched: bool = False,
    exclude_handles: set[str] | None = None,
) -> int:
    inserted = 0
    pending_writes = False
    try:
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
                max_followers=max_followers,
            )
            duplicate_email = (
                evaluation.lead is not None and repository.exists_by_email(evaluation.lead.email)
            )
            skip_code = "duplicate_email" if duplicate_email else evaluation.skip_code
            norm_handle = candidate.handle.removeprefix("@").lower() if candidate.handle else ""
            candidate_found_via = (
                found_via_by_handle.get(norm_handle, found_via)
                if found_via_by_handle is not None
                else found_via
            )
            repository.record_scraped_profile(
                handle=candidate.handle,
                niche=candidate.niche,
                source=candidate.source,
                skip_reason=skip_code,
                found_via=candidate_found_via,
                bio_link_enriched=mark_bio_link_enriched,
                commit=False,
            )
            if exclude_handles is not None and candidate.handle:
                exclude_handles.add(norm_handle)
            # Only harvest hashtags from on-niche creators at or above the
            # follower bar so off-niche / rejected-group accounts never pollute
            # the discovery flywheel.
            if candidate.discovered_hashtags and skip_code not in (
                "low_followers",
                "high_followers",
                "off_niche",
            ):
                repository.record_discovered_hashtags(
                    candidate.discovered_hashtags,
                    niche=candidate.niche,
                    commit=False,
                )
            pending_writes = True

            # Commit per candidate so SQLite WAL write locks are never held across
            # the next `await` (network/Playwright I/O) when another scraper runs
            # concurrently against the same database.
            if evaluation.lead is None:
                repository.commit()
                pending_writes = False
                logger.info("skipped @%s: %s", candidate.handle or "unknown", evaluation.skip_reason)
                continue

            lead = replace(evaluation.lead, found_via=candidate_found_via)
            if duplicate_email:
                repository.commit()
                pending_writes = False
                logger.info("skipped @%s: email already exists (%s)", lead.handle, lead.email)
                continue

            if repository.insert_lead(lead):
                pending_writes = False
                logger.info("inserted @%s email=%s", lead.handle, lead.email)
                logger.info("sending notification for @%s", lead.handle)
                try:
                    notifier.send(lead)
                except Exception:
                    logger.exception("failed to send notification for @%s", lead.handle)
                else:
                    repository.mark_notified(lead.email, lead.platform)
                    logger.info("notification marked sent for @%s", lead.handle)
                inserted += 1
            else:
                repository.commit()
                pending_writes = False
    finally:
        if pending_writes:
            repository.commit()
    return inserted
