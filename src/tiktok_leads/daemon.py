from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

from tiktok_leads.db import LeadRepository
from tiktok_leads.factory import build_source
from tiktok_leads.niches import hashtags_for_niche, search_terms_for_niche
from tiktok_leads.notifiers import Notifier
from tiktok_leads.runner import scrape_handles, scrape_hashtag, scrape_search
from tiktok_leads.settings import Settings
from tiktok_leads.sources.tiktokapi_source import TikTokBlockedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkItem:
    kind: str  # "hashtag" | "search" | "recheck"
    niche: str
    value: str  # hashtag, search phrase, or handle
    harvested: bool = False  # hashtag came from the harvesting flywheel

    @property
    def label(self) -> str:
        if self.kind == "hashtag":
            return f"#{self.value}{'*' if self.harvested else ''}({self.niche})"
        if self.kind == "search":
            return f"search:{self.value!r}({self.niche})"
        return f"recheck:@{self.value}({self.niche})"


def _build_queue(settings: Settings, repository: LeadRepository, niches: list[str]) -> list[WorkItem]:
    """Build a shuffled work queue for one sweep: seed hashtags + harvested
    hashtags + user-searches + stale re-checks."""
    items: list[WorkItem] = []
    seen: set[tuple[str, str, str]] = set()

    def add(item: WorkItem) -> None:
        key = (item.kind, item.niche, item.value.lower())
        if key not in seen:
            seen.add(key)
            items.append(item)

    for niche in niches:
        for tag in hashtags_for_niche(niche):
            add(WorkItem("hashtag", niche, tag))
        if settings.discovery_use_harvested_hashtags:
            for tag in repository.due_hashtags(
                niche=niche,
                limit=settings.discovery_harvested_hashtags_per_niche,
                min_times_seen=settings.discovery_harvested_min_mentions,
                not_scraped_within_days=settings.discovery_harvested_recrawl_after_days,
            ):
                add(WorkItem("hashtag", niche, tag, harvested=True))
        if settings.effective_discovery_search:
            for term in search_terms_for_niche(niche):
                add(WorkItem("search", niche, term))

    if settings.recheck_enabled:
        for handle, handle_niche in repository.handles_due_for_recheck(
            after_days=settings.recheck_after_days,
            limit=settings.recheck_handles_per_sweep,
        ):
            add(WorkItem("recheck", handle_niche, handle))

    random.shuffle(items)
    return items


async def run_daemon(
    settings: Settings,
    repository: LeadRepository,
    notifier: Notifier,
    *,
    niches: list[str],
    limit: int,
) -> None:
    """Run forever: scrape a small slice of work each cycle, then rest.

    Discovery sources (hashtags, harvested hashtags, user-search, re-checks)
    keep fresh creators flowing in as the dataset grows. A sustained block
    raises TikTokBlockedError; we back off for hours and resume — never crash,
    never lose progress (dedup + harvested tags + skip reasons live in SQLite).
    """
    logger.info(
        "daemon starting: niches=%s per_cycle=%s cycle_sleep=%.0fmin",
        ", ".join(niches),
        settings.effective_burst_size,
        settings.effective_rest_seconds / 60,
    )

    queue: list[WorkItem] = []
    block_backoff = settings.daemon_block_backoff_initial_seconds

    try:
        while True:
            if not queue:
                queue = _build_queue(settings, repository, niches)
                if not queue:
                    logger.error(
                        "no work for niches=%s; sleeping before retry", ", ".join(niches)
                    )
                    await asyncio.sleep(settings.effective_rest_seconds)
                    continue
                logger.info("daemon: new sweep queued with %s work item(s)", len(queue))

            batch = [queue.pop() for _ in range(min(settings.effective_burst_size, len(queue)))]
            logger.info("daemon: starting cycle: %s", ", ".join(i.label for i in batch))

            try:
                inserted = await _run_batch(settings, repository, notifier, batch, limit)
            except TikTokBlockedError as error:
                queue.extend(batch)
                random.shuffle(queue)
                sleep_for = block_backoff + random.uniform(
                    0, settings.daemon_block_backoff_jitter_seconds
                )
                logger.warning(
                    "daemon: TikTok is blocking (%s); backing off %.0fmin then resuming",
                    error,
                    sleep_for / 60,
                )
                await asyncio.sleep(sleep_for)
                block_backoff = min(
                    block_backoff * settings.daemon_block_backoff_multiplier,
                    settings.daemon_block_backoff_max_seconds,
                )
                continue
            except Exception:
                logger.exception("daemon: unexpected error in cycle; pausing then continuing")
                await asyncio.sleep(settings.daemon_error_cooldown_seconds)
                continue

            block_backoff = settings.daemon_block_backoff_initial_seconds
            cycle_sleep = settings.effective_rest_seconds + random.uniform(
                0, settings.effective_rest_jitter_seconds
            )
            logger.info(
                "daemon: cycle done (%s new lead(s)); sleeping %.0fmin", inserted, cycle_sleep / 60
            )
            await asyncio.sleep(cycle_sleep)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("daemon: shutting down")
        raise


async def _run_batch(
    settings: Settings,
    repository: LeadRepository,
    notifier: Notifier,
    batch: list[WorkItem],
    limit: int,
) -> int:
    """Open one source session and run each work item in this batch."""
    inserted = 0
    async with build_source(settings) as source:
        for item in batch:
            if item.kind == "recheck":
                # Re-check a stale handle directly — no exclusion, we *want* to
                # re-evaluate it in case a public email was added.
                inserted += await scrape_handles(
                    source,
                    repository,
                    notifier,
                    handles=[item.value],
                    niche=item.niche,
                    min_followers=settings.min_followers,
                    min_average_views=settings.min_average_views,
                )
                continue

            exclude_handles = repository.excluded_handles(settings.recheck_after_days)
            if item.kind == "search":
                inserted += await scrape_search(
                    source,
                    repository,
                    notifier,
                    query=item.value,
                    niche=item.niche,
                    limit=settings.discovery_search_results_per_term,
                    min_followers=settings.min_followers,
                    min_average_views=settings.min_average_views,
                    exclude_handles=exclude_handles,
                )
            else:  # hashtag (seed or harvested)
                inserted += await scrape_hashtag(
                    source,
                    repository,
                    notifier,
                    hashtag=item.value,
                    niche=item.niche,
                    limit=limit,
                    min_followers=settings.min_followers,
                    min_average_views=settings.min_average_views,
                    exclude_handles=exclude_handles,
                )
                if item.harvested:
                    repository.mark_hashtag_scraped(item.value, niche=item.niche)
    return inserted
