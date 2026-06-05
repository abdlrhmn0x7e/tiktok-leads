from __future__ import annotations

import argparse
import asyncio
import logging
import random

from tiktok_leads.db import LeadRepository
from tiktok_leads.niches import hashtags_for_niche
from tiktok_leads.notifiers import build_notifier
from tiktok_leads.runner import scrape_handles, scrape_hashtag
from tiktok_leads.settings import Settings
from tiktok_leads.sources import TikTokApiSource


def main() -> None:
    asyncio.run(async_main())


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Find TikTok leads and store them in SQLite.")
    parser.add_argument("--niche", required=True, help="Niche label, e.g. mom, fitness, lifestyle")
    parser.add_argument("--handle", action="append", default=[], help="TikTok handle to inspect")
    parser.add_argument("--hashtag", action="append", default=[], help="TikTok hashtag to crawl")
    parser.add_argument("--limit", type=int, default=30, help="Maximum hashtag videos to inspect")
    parser.add_argument("--init-db", action="store_true", help="Only initialize the SQLite schema")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    args = parser.parse_args()
    configure_logging(args.log_level)

    settings = Settings()
    if settings.tiktok_suppress_library_errors:
        logging.getLogger("TikTokApi.tiktok").setLevel(logging.CRITICAL)
    repository = LeadRepository(settings.database_path)
    repository.initialize()

    if args.init_db:
        print(f"Initialized database at {settings.database_path}")
        repository.close()
        return

    hashtags = args.hashtag or list(hashtags_for_niche(args.niche))
    if settings.shuffle_hashtags and not args.hashtag:
        random.shuffle(hashtags)
    if not args.handle and not hashtags:
        parser.error("provide at least one --handle or --hashtag, or use a configured niche")
    if args.hashtag:
        logging.info("using explicit hashtag(s): %s", ", ".join(f"#{tag.removeprefix('#')}" for tag in hashtags))
    else:
        logging.info("using preset hashtag(s) for niche=%s: %s", args.niche, ", ".join(f"#{tag}" for tag in hashtags))

    notifier = build_notifier(settings)
    inserted = 0
    try:
        async with TikTokApiSource(
            ms_token=settings.tiktok_ms_token,
            recent_video_count=settings.recent_video_count,
            headless=settings.tiktok_browser_headless,
            browser=settings.tiktok_browser,
            starting_url=settings.tiktok_starting_url,
            session_timeout_ms=settings.tiktok_session_timeout_ms,
            session_retries=settings.tiktok_session_retries,
            request_delay_seconds=settings.tiktok_request_delay_seconds,
            request_jitter_seconds=settings.tiktok_request_jitter_seconds,
            max_consecutive_blocked_profiles=settings.tiktok_max_consecutive_blocked_profiles,
            block_cooldown_seconds=settings.tiktok_block_cooldown_seconds,
            max_block_cooldowns_per_hashtag=settings.tiktok_max_block_cooldowns_per_hashtag,
            restart_session_on_block=settings.tiktok_restart_session_on_block,
            restart_session_between_hashtags=settings.tiktok_restart_session_between_hashtags,
            proxy=build_proxy(settings),
        ) as source:
            if args.handle:
                inserted += await scrape_handles(
                    source,
                    repository,
                    notifier,
                    handles=args.handle,
                    niche=args.niche,
                    min_followers=settings.min_followers,
                    min_average_views=settings.min_average_views,
                )
            for hashtag in hashtags:
                exclude_handles = repository.seen_handles()
                logging.info(
                    "excluding %s previously evaluated handle(s)",
                    len(exclude_handles),
                )
                inserted += await scrape_hashtag(
                    source,
                    repository,
                    notifier,
                    hashtag=hashtag,
                    niche=args.niche,
                    limit=args.limit,
                    min_followers=settings.min_followers,
                    min_average_views=settings.min_average_views,
                    exclude_handles=exclude_handles,
                )
    finally:
        repository.close()

    print(f"Inserted {inserted} new lead(s).")


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_proxy(settings: Settings) -> dict[str, str] | None:
    if not settings.proxy_server:
        return None
    proxy = {"server": settings.proxy_server}
    if settings.proxy_username:
        proxy["username"] = settings.proxy_username
    if settings.proxy_password:
        proxy["password"] = settings.proxy_password
    return proxy
