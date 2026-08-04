from __future__ import annotations

import argparse
import asyncio
import logging
import random

import requests

from tiktok_leads.daemon import run_daemon
from tiktok_leads.db import LeadRepository
from tiktok_leads.factory import build_proxy_provider, build_source
from tiktok_leads.models import Lead
from tiktok_leads.niches import hashtags_for_niche
from tiktok_leads.notifiers import build_notifier
from tiktok_leads.runner import scrape_handles, scrape_hashtag
from tiktok_leads.settings import Settings
from tiktok_leads.sources.tiktokapi_source import TikTokBlockedError


def main() -> None:
    asyncio.run(async_main())


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Find TikTok leads and store them in SQLite.")
    parser.add_argument("--niche", help="Niche label, e.g. mom, fitness, lifestyle")
    parser.add_argument("--handle", action="append", default=[], help="TikTok handle to inspect")
    parser.add_argument("--hashtag", action="append", default=[], help="TikTok hashtag to crawl")
    parser.add_argument("--limit", type=int, default=30, help="Maximum hashtag videos to inspect")
    parser.add_argument("--init-db", action="store_true", help="Only initialize the SQLite schema")
    parser.add_argument("--stats", action="store_true", help="Print pipeline stats and exit")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run forever: scrape a slice of hashtags each cycle and survive blocks. "
        "Accepts comma-separated niches, e.g. --niche fitness,mom",
    )
    parser.add_argument("--test-notification", action="store_true", help="Send a fake lead notification and exit")
    parser.add_argument("--test-proxy", action="store_true", help="Test configured proxy connectivity and exit")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    video_hashtags = parser.add_mutually_exclusive_group()
    video_hashtags.add_argument(
        "--crawl-video-hashtags",
        dest="crawl_video_hashtags",
        action="store_true",
        default=None,
        help="Crawl hashtags discovered from scraped videos in daemon mode",
    )
    video_hashtags.add_argument(
        "--no-crawl-video-hashtags",
        dest="crawl_video_hashtags",
        action="store_false",
        help="Do not crawl hashtags discovered from scraped videos in daemon mode",
    )
    args = parser.parse_args()
    configure_logging(args.log_level)
    if not args.niche and not (args.stats or args.init_db or args.test_proxy or args.test_notification):
        parser.error("--niche is required unless using --stats, --init-db, --test-proxy, or --test-notification")

    settings = Settings()
    if args.crawl_video_hashtags is not None:
        settings.discovery_use_harvested_hashtags = args.crawl_video_hashtags
    if settings.tiktok_suppress_library_errors:
        logging.getLogger("TikTokApi.tiktok").setLevel(logging.CRITICAL)

    if args.test_proxy:
        test_proxy(settings)
        return

    if args.test_notification:
        notifier = build_notifier(settings)
        notifier.send(
            Lead(
                handle="test_creator_name",
                profile_url="https://www.tiktok.com/@test_creator_name",
                niche=args.niche or "test",
                email="test@example.com",
                followers_count=125_000,
                average_views=35_000,
                source="test",
            )
        )
        print("Sent test notification.")
        return

    repository = LeadRepository(settings.database_path)
    repository.initialize()

    if args.init_db:
        print(f"Initialized database at {settings.database_path}")
        repository.close()
        return

    if args.stats:
        print_stats(repository)
        repository.close()
        return

    if args.daemon:
        niches = [n.strip() for n in args.niche.split(",") if n.strip()]
        notifier = build_notifier(settings)
        try:
            await run_daemon(settings, repository, notifier, niches=niches, limit=args.limit)
        finally:
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
        async with build_source(settings) as source:
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
                    feed_cursor_max_age_days=settings.feed_cursor_max_age_days,
                )
    except TikTokBlockedError as error:
        logging.warning("stopped early because TikTok is blocking requests: %s", error)
        print(
            "Stopped early because TikTok is blocking requests. "
            f"Inserted {inserted} new lead(s) before the block. "
            "Use --daemon to back off and resume automatically, or slow the scrape/change proxy."
        )
        raise SystemExit(2) from None
    finally:
        repository.close()

    print(f"Inserted {inserted} new lead(s).")


def print_stats(repository: LeadRepository) -> None:
    stats = repository.stats()
    print(f"Total leads: {stats['total_leads']}  (last 24h: {stats['leads_last_24h']})")
    print(f"Profiles evaluated: {stats['evaluated_total']}  (last 24h: {stats['evaluated_last_24h']})")
    print(f"Harvested hashtags: {stats['harvested_hashtags']}")
    print(f"Profiles awaiting email re-check: {stats['pending_recheck']}")

    if stats["leads_by_niche"]:
        print("\nLeads by niche:")
        for niche, count in stats["leads_by_niche"]:
            print(f"  {niche:<20} {count}")

    if stats["leads_last_7_days"]:
        print("\nLeads per day (last 7 days):")
        for day, count in stats["leads_last_7_days"]:
            print(f"  {day}  {count}")

    if stats["skip_reasons"]:
        print("\nEvaluation outcomes:")
        for reason, count in stats["skip_reasons"]:
            print(f"  {reason:<20} {count}")

    if stats["lead_sources"]:
        print("\nSources that produced leads:")
        for via, count in stats["lead_sources"]:
            print(f"  {via:<30} {count}")


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def test_proxy(settings: Settings) -> None:
    provider = build_proxy_provider(settings)
    if provider is None:
        raise SystemExit("No proxy configured (set PROXY_SERVERS, PROXY_SERVER, or WEBSHARE_API_KEY).")

    try:
        proxies = provider.list_proxies()
    except Exception as error:
        raise SystemExit(f"Could not load proxy list: {error}") from error
    if not proxies:
        raise SystemExit("Proxy provider returned an empty proxy list.")

    failures = 0
    for proxy in proxies:
        endpoint = f"{proxy.proxy_address}:{proxy.port}"
        auth = f"{proxy.username}:{proxy.password}@" if proxy.username and proxy.password else ""
        proxy_url = f"http://{auth}{endpoint}"
        try:
            response = requests.get(
                "https://api.ipify.org?format=json",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.ProxyError as error:
            failures += 1
            print(
                f"FAIL {endpoint}: the proxy rejected the connection. "
                "Check plan/payment/quota, credentials, and whether HTTPS CONNECT is allowed. "
                f"Details: {error}"
            )
        except requests.exceptions.RequestException as error:
            failures += 1
            print(f"FAIL {endpoint}: {error}")
        else:
            print(f"OK   {endpoint} -> {response.text}")
    print(f"{len(proxies) - failures}/{len(proxies)} proxies OK; the scraper opens one session per proxy.")
    if failures:
        raise SystemExit(1)
