from __future__ import annotations

import argparse
import asyncio
import logging
import random

import requests

from tiktok_leads.daemon import run_daemon
from tiktok_leads.db import LeadRepository
from tiktok_leads.factory import build_proxy, build_source
from tiktok_leads.models import Lead
from tiktok_leads.niches import hashtags_for_niche
from tiktok_leads.notifiers import build_notifier
from tiktok_leads.runner import scrape_handles, scrape_hashtag
from tiktok_leads.settings import Settings


def main() -> None:
    asyncio.run(async_main())


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Find TikTok leads and store them in SQLite.")
    parser.add_argument("--niche", required=True, help="Niche label, e.g. mom, fitness, lifestyle")
    parser.add_argument("--handle", action="append", default=[], help="TikTok handle to inspect")
    parser.add_argument("--hashtag", action="append", default=[], help="TikTok hashtag to crawl")
    parser.add_argument("--limit", type=int, default=30, help="Maximum hashtag videos to inspect")
    parser.add_argument("--init-db", action="store_true", help="Only initialize the SQLite schema")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run forever: scrape a slice of hashtags each cycle and survive blocks. "
        "Accepts comma-separated niches, e.g. --niche fitness,mom",
    )
    parser.add_argument("--test-notification", action="store_true", help="Send a fake lead notification and exit")
    parser.add_argument("--test-proxy", action="store_true", help="Test configured proxy connectivity and exit")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    args = parser.parse_args()
    configure_logging(args.log_level)

    settings = Settings()
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
                niche=args.niche,
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


def test_proxy(settings: Settings) -> None:
    proxy = build_proxy(settings)
    if proxy is None:
        raise SystemExit("PROXY_SERVER is not configured.")

    server = proxy["server"]
    if proxy.get("username") and proxy.get("password"):
        proxy_url = server.replace(
            "://",
            f"://{proxy['username']}:{proxy['password']}@",
            1,
        )
    else:
        proxy_url = server

    try:
        response = requests.get(
            "https://api.ipify.org?format=json",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.ProxyError as error:
        raise SystemExit(
            "Proxy test failed: the proxy rejected the connection. "
            "Check plan/payment/quota, credentials, and whether HTTPS CONNECT is allowed.\n"
            f"Details: {error}"
        ) from error
    except requests.exceptions.RequestException as error:
        raise SystemExit(f"Proxy test failed: {error}") from error
    print(f"Proxy OK via {server}: {response.text}")
