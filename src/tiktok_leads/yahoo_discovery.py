"""Discover TikTok creators through Yahoo web search.

`site:tiktok.com "gmail.com" <fitness term>` results show each profile's
bio and follower count in the snippet, so creators arrive pre-filtered on
email and followers before any TikTok request. Yahoo blocks Python HTTP
clients by TLS fingerprint but serves plain curl.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import subprocess
from typing import NamedTuple
from urllib.parse import quote_plus, unquote

from tiktok_leads.db import LeadRepository

logger = logging.getLogger(__name__)

# 66 of 104 phrases still had results on page 4; a sample of 8 kept giving new creators to page 13.
PAGES_PER_QUERY = 15
# Yahoo serves 5 results a page; stepping by 7 skipped two of every seven.
RESULTS_PER_PAGE = 5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_HANDLE = re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,24})")
# Snippets often space out the address ("name @ gmail . com").
_EMAIL = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._%+-]*)\s*@\s*([A-Za-z0-9-]+)\s*\.\s*(com|net|org|co\.uk|co|io|me|fr|de|ca|au)\b",
    re.IGNORECASE,
)
_FOLLOWERS = re.compile(r"(\d[\d.,]*)\s*([KkMm]?)\s*Followers")


class YahooHit(NamedTuple):
    handle: str
    email: str | None
    followers: int | None


def parse_results(page: str) -> list[YahooHit]:
    hits = []
    for block in page.split('class="dd algo')[1:]:
        handle = _HANDLE.search(unquote(unquote(block)))
        if not handle:
            continue
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", block)))
        email = _EMAIL.search(text)
        followers = _FOLLOWERS.search(text)
        hits.append(
            YahooHit(
                handle=handle.group(1).lower().rstrip("."),
                email=f"{email.group(1)}@{email.group(2)}.{email.group(3)}".lower() if email else None,
                followers=_count(*followers.groups()) if followers else None,
            )
        )
    return hits


def _count(value: str, suffix: str) -> int | None:
    try:
        number = float(value.replace(",", ""))
    except ValueError:  # "1.234.567" style counts
        return None
    return int(number * {"k": 1_000, "m": 1_000_000}.get(suffix.lower(), 1))


def fetch_page(query: str, page: int) -> str:
    search = quote_plus(f"site:tiktok.com {query}")
    url = f"https://search.yahoo.com/search?p={search}&b={1 + RESULTS_PER_PAGE * page}"
    # Yahoo answers curl's HTTP/2 with an empty 500 (since 2026-11-10); HTTP/1.1 gets the page.
    result = subprocess.run(
        ["curl", "-s", "--http1.1", "-w", "\n%{http_code}", "-A", USER_AGENT,
         "-H", "Accept-Language: en-US,en;q=0.9", "-H", "Accept: text/html", url],
        capture_output=True, text=True, timeout=30, check=True,
    )
    body, status = result.stdout.rsplit("\n", 1)
    if status != "200":
        raise RuntimeError(f"Yahoo returned HTTP {status}")
    return body


def cursor_key(query: str) -> str:
    return f"yahoo:{query}"


def is_done(repository: LeadRepository, query: str, *, max_age_days: int) -> bool:
    return repository.get_feed_cursor(cursor_key(query), max_age_days=max_age_days) >= PAGES_PER_QUERY


async def next_page_emails(
    repository: LeadRepository,
    query: str,
    *,
    min_followers: int,
    exclude_handles: set[str],
    max_age_days: int,
) -> dict[str, str] | None:
    """Fetch the query's next unread page and advance its cursor. Returns
    handle -> snippet email for unevaluated creators at or above
    min_followers. A failed fetch returns None and leaves the cursor so the
    page reruns."""
    key = cursor_key(query)
    page = repository.get_feed_cursor(key, max_age_days=max_age_days)
    try:
        hits = parse_results(await asyncio.to_thread(fetch_page, query, page))
    except (subprocess.SubprocessError, RuntimeError):
        logger.exception("yahoo: failed to fetch %r page %s", query, page)
        return None
    # A page with no TikTok results means the query ran out.
    repository.set_feed_cursor(key, page + 1 if hits else PAGES_PER_QUERY)
    usable = {
        hit.handle: hit.email
        for hit in hits
        if hit.email and (hit.followers or 0) >= min_followers and hit.handle not in exclude_handles
    }
    logger.info(
        "yahoo: %r page %s: %s TikTok result(s), %s new with email and >=%s followers",
        query, page, len(hits), len(usable), min_followers,
    )
    return usable
