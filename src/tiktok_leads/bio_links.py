from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urlsplit

import requests

from tiktok_leads.email_extractor import extract_emails

logger = logging.getLogger(__name__)

# Link-in-bio services where creators park their contact info. A bare mention
# of one of these in a bio (no scheme needed) is a strong signal the profile
# is worth a full fetch even when the bio text itself has no email.
LINK_SERVICE_DOMAINS = (
    "linktr.ee",
    "beacons.ai",
    "stan.store",
    "komi.io",
    "snipfeed.co",
    "campsite.bio",
    "hoo.be",
    "lnk.bio",
    "bio.site",
    "linkin.bio",
    "taplink.cc",
    "milkshake.app",
    "solo.to",
    "allmylinks.com",
    "direct.me",
    "flowpage.com",
    "linkpop.com",
    "pillar.io",
)

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s'\"<>]+")
_SERVICE_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(d) for d in LINK_SERVICE_DOMAINS) + r")/[^\s'\"<>]*"
)
_MAILTO_RE = re.compile(r"(?i)mailto:([^\s'\"<>?]+)")

# The email regex happily matches asset filenames like logo@2x.png inside
# HTML; anything whose "TLD" is a file extension is noise, not a contact.
_ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".mp4", ".webm",
)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_MAX_RESPONSE_BYTES = 600_000


def bio_hints_link_page(bio: str) -> bool:
    """True when the bio references a link-in-bio service or any explicit URL —
    the cases where an email might live one hop away."""
    if not bio:
        return False
    return bool(_SERVICE_RE.search(bio) or _URL_RE.search(bio))


def candidate_urls(bio: str, links: list[str]) -> list[str]:
    """URLs worth fetching for a contact email: the profile's bio link plus any
    URLs written in the bio text, link-in-bio services first."""
    raw: list[str] = []
    for link in links:
        normalized = _normalize_url(link)
        if normalized:
            raw.append(normalized)
    for match in _SERVICE_RE.findall(bio or ""):
        normalized = _normalize_url(match)
        if normalized:
            raw.append(normalized)
    for match in _URL_RE.findall(bio or ""):
        normalized = _normalize_url(match)
        if normalized:
            raw.append(normalized)

    seen: set[str] = set()
    ordered: list[str] = []
    for url in raw:
        key = url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            ordered.append(url)
    # Known link services first — they're the most likely to list an email.
    ordered.sort(key=lambda u: 0 if _is_link_service(u) else 1)
    return ordered


def emails_from_bio_links(
    bio: str,
    links: list[str],
    *,
    timeout_seconds: float = 10.0,
    max_fetches: int = 2,
) -> list[str]:
    """Fetch the profile's bio-link page(s) and extract contact emails.
    These are ordinary public pages (Linktree etc.) fetched directly — no
    proxy, no TikTok involvement. Returns [] when nothing turns up."""
    emails: list[str] = []
    for url in candidate_urls(bio, links)[:max_fetches]:
        try:
            found = _emails_from_url(url, timeout_seconds=timeout_seconds)
        except requests.RequestException as error:
            logger.info("bio link fetch failed for %s: %s", url, error)
            continue
        for email in found:
            if email not in emails:
                emails.append(email)
        if emails:
            break
    return emails


def _emails_from_url(url: str, *, timeout_seconds: float) -> list[str]:
    response = requests.get(
        url,
        headers=_FETCH_HEADERS,
        timeout=timeout_seconds,
        stream=True,
        allow_redirects=True,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if content_type and "text" not in content_type and "json" not in content_type:
        return []
    body = response.raw.read(_MAX_RESPONSE_BYTES, decode_content=True)
    html = body.decode(response.encoding or "utf-8", errors="replace")

    # mailto: links are the highest-confidence signal and survive URL-encoding.
    chunks = [unquote(m) for m in _MAILTO_RE.findall(html)]
    chunks.append(html)
    return [email for email in extract_emails(*chunks) if not _looks_like_asset(email)]


def _looks_like_asset(email: str) -> bool:
    return email.endswith(_ASSET_SUFFIXES)


def _is_link_service(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host in LINK_SERVICE_DOMAINS


def _normalize_url(raw: str) -> str | None:
    raw = (raw or "").strip().strip(".,;:)(")
    if not raw:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    hostname = parsed.hostname or ""
    if "." not in hostname:
        return None
    return raw
