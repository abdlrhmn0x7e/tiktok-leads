from __future__ import annotations

import html
import logging
import re
from urllib.parse import unquote, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.niches import bio_on_niche

logger = logging.getLogger(__name__)

# Link-in-bio services where creators park their contact info. A bare mention
# of one of these in a bio (no scheme needed) is a strong signal the profile
# is worth checking even when the bio text itself has no email.
LINK_SERVICE_DOMAINS = (
    "linktr.ee",
    "beacons.ai",
    "beacons.page",
    "stan.store",
    "komi.io",
    "snipfeed.co",
    "campsite.bio",
    "hoo.be",
    "lnk.bio",
    "bio.site",
    "link.me",
    "linkin.bio",
    "linkr.bio",
    "taplink.cc",
    "tap.bio",
    "milkshake.app",
    "msha.ke",
    "solo.to",
    "allmylinks.com",
    "direct.me",
    "flowpage.com",
    "linkpop.com",
    "pillar.io",
    "carrd.co",
    "bento.me",
    "pensight.com",
    "zaap.bio",
    "shor.by",
    "znap.link",
    "liinks.co",
    "pass.bio",
    "withkoji.com",
    "my.bio",
    "shopltk.com",
    "liketk.it",
    "ltks.app",
)

# Social, storefront, and fundraiser hosts that never carry a creator's own
# email (or only return 403/404/platform support addresses).
_SKIP_FETCH_DOMAINS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "instagram.com",
        "tiktok.com",
        "facebook.com",
        "fb.com",
        "twitter.com",
        "x.com",
        "snapchat.com",
        "twitch.tv",
        "discord.gg",
        "discord.com",
        "spotify.com",
        "music.apple.com",
        "soundcloud.com",
        "amazon.com",
        "amzn.to",
        "etsy.com",
        "gofund.me",
        "gofundme.com",
        "patreon.com",
        "onlyfans.com",
        "tokopedia.com",
        "shopee.com",
        "temu.com",
        "shein.com",
        "walmart.com",
        "target.com",
        "ebay.com",
        "payhip.com",
        "venmo.com",
        "cash.app",
        "paypal.me",
        "buymeacoffee.com",
        "ko-fi.com",
        "t.me",
        "wa.me",
        "whatsapp.com",
        "reddit.com",
        "pinterest.com",
        "linkedin.com",
        "threads.net",
        "shopmy.us",
        "skool.com",
        "a.co",
        "docs.google.com",
        "drive.google.com",
        "forms.gle",
        "onelink.me",
        "apps.apple.com",
        "play.google.com",
    }
)

# Restrict URL tokens to valid ASCII URL characters so trailing emojis
# (e.g. "https://www.fitnessbyfaz.com👇") don't break URL parsing.
_URL_CHARS = r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"
_URL_RE = re.compile(rf"(?i)\b(?:https?://|www\.){_URL_CHARS}")
_SERVICE_RE = re.compile(
    r"(?i)\b(?:"
    + "|".join(re.escape(d) for d in LINK_SERVICE_DOMAINS)
    + r")/[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*"
)
_MAILTO_RE = re.compile(r"(?i)mailto:([^\s'\"<>?\\]+)")
_CF_EMAIL_RE = re.compile(
    r'(?i)(?:data-cfemail=["\']([0-9a-f]{4,})["\']|/cdn-cgi/l/email-protection#([0-9a-f]{4,}))'
)
_JSON_UNICODE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_SCRIPT_RE = re.compile(
    r'(?is)<script\b[^>]*(?:id=["\']__NEXT_DATA__["\']|type=["\']application/(?:ld\+)?json["\'])[^>]*>(.*?)</script\s*>'
)
_STYLE_OR_SCRIPT_RE = re.compile(r"(?is)<(?:style|script|noscript)\b.*?</(?:style|script|noscript)\s*>")

# The email regex happily matches asset filenames like logo@2x.png inside
# HTML; anything whose "TLD" is a file extension is noise, not a contact.
_ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".mp4", ".webm",
)

# Shop, host, link-in-bio, and placeholder domains that never belong to a creator.
_PLATFORM_EMAIL_DOMAINS = (
    "amazon.com",
    "temu.com",
    "godaddy.com",
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "dominio.com",
    "email.com",
    "test.com",
    "yoursite.com",
    "yourdomain.com",
    "company.com",
    "website.com",
    "sentry.io",
    "wixpress.com",
    "wix.com",
    "squarespace.com",
    "shopify.com",
    "webflow.io",
    "linktr.ee",
    "beacons.ai",
    "stan.store",
    "link.me",
    "bio.site",
    "lnk.bio",
    "hoo.be",
    "komi.io",
    "campsite.bio",
    "pensight.com",
    "carrd.co",
    "shopltk.com",
    "patreon.com",
    "etsy.com",
    "gofundme.com",
    "calendly.com",
    "typeform.com",
    "jotform.com",
    "kajabi.com",
    "teachable.com",
    "trainerize.me",
    "truecoach.co",
    "everfit.io",
    "wordpress.org",
    "cloudflare.com",
    "google.com",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "apple.com",
    "microsoft.com",
    "stripe.com",
    "paypal.com",
    "klaviyo.com",
    "mailchimp.com",
    "hubspot.com",
    "canva.com",
    "notion.so",
    "shopmy.us",
    "skool.com",
    "whop.com",
    "circle.so",
    "beacons.page",
    "bit.ly",
)

_NOISE_LOCAL_PARTS = frozenset(
    {
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "privacy",
        "legal",
        "abuse",
        "dmca",
        "copyright",
        "guidelines",
        "security",
        "postmaster",
        "webmaster",
        "hostmaster",
        "mailer-daemon",
        "support",
        "help",
        "helpdesk",
        "billing",
        "careers",
        "unsubscribe",
        "user",
        "usuario",
        "yourname",
        "placeholder",
        "example",
        "test",
    }
)
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_UNICODE_PREFIX_RE = re.compile(r"^u00[0-9a-f]{2}")
_TITLE_RE = re.compile(
    r'(?is)(?:<title[^>]*>(.*?)</title>|property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\'])'
)
_BIO_CTA_RE = re.compile(
    r"(?i)(?:link in bio|links? below|apply\b|1[:\- ]?on[:\- ]?1|1:1|coaching|programs?|workout plans?"
    r"|guides?|collabs?|inquir(?:y|ies)|business|bookings?|👇|⬇️|⬇|⤵️|⤵|🔗|📩|💌|📧)"
)
# Platforms that answer plain HTTP 200 and reuse creator handles cleanly.
HANDLE_PROBE_TEMPLATES = (
    "https://linktr.ee/{handle}",
    "https://stan.store/{handle}",
    "https://bio.site/{handle}",
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

_SESSION = requests.Session()
_SESSION.headers.update(_FETCH_HEADERS)
_ADAPTER = HTTPAdapter(pool_connections=8, pool_maxsize=8)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)


def bio_hints_link_page(bio: str) -> bool:
    """True when the bio references a fetchable link-in-bio service or personal
    URL (ignoring social/storefront URLs that never expose a creator's email)."""
    if not bio:
        return False
    return bool(candidate_urls(bio, []))


def bio_suggests_external_link(bio: str) -> bool:
    """True when the bio has a link-in-bio CTA (e.g. 'link in bio', 'apply',
    '1:1 coaching', pointer/envelope emoji) suggesting a clickable TikTok Bio Link."""
    return bool(bio and _BIO_CTA_RE.search(bio))


def probe_handle_bio_links(
    handle: str,
    *,
    niche: str | None = None,
    timeout_seconds: float = 6.0,
) -> list[str]:
    """Probe Linktree/Stan/Bio.site directly by TikTok handle (zero TikTok
    requests). Only extracts emails when the page verifies it belongs to
    `handle` (either links back to tiktok.com/@handle, or matches a >=6-char
    handle in its title AND matches the niche's content terms)."""
    clean_handle = handle.removeprefix("@").strip().lower()
    if not clean_handle:
        return []
    for template in HANDLE_PROBE_TEMPLATES:
        url = template.format(handle=clean_handle)
        try:
            page_html = _fetch_html(url, timeout_seconds=timeout_seconds)
        except requests.RequestException:
            continue
        if not page_html:
            continue
        if not _page_matches_creator(page_html, clean_handle, niche=niche):
            continue
        found = extract_emails_from_html(page_html)
        if found:
            logger.info("found email via handle probe %s for @%s: %s", url, clean_handle, found[0])
            return found
    return []


def _page_matches_creator(page_html: str, handle: str, *, niche: str | None) -> bool:
    lower_html = page_html.lower()
    # Highest-confidence match: explicit TikTok backlink to this exact handle.
    if re.search(rf"tiktok\.com/@{re.escape(handle)}(?![a-z0-9_.])", lower_html):
        return True
    # Fallback for link pages that don't link back to TikTok: require a
    # sufficiently specific handle (>=6 alnum chars) matching the page title
    # AND niche-relevant copy on the page.
    alnum = re.sub(r"[^a-z0-9]", "", handle)
    if len(alnum) < 6:
        return False
    titles = " ".join(a or b for a, b in _TITLE_RE.findall(page_html))
    title_alnum = re.sub(r"[^a-z0-9]", "", titles.lower())
    if alnum not in title_alnum:
        return False
    visible = _unescape_text(_STYLE_OR_SCRIPT_RE.sub(" ", page_html))
    return niche is None or bio_on_niche(niche, visible)


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
        if _is_skipped_host(url):
            continue
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


def extract_emails_from_html(page_html: str) -> list[str]:
    """Extract clean creator emails from a link-in-bio or personal page.

    Priority order:
      1. mailto: links and decoded Cloudflare data-cfemail addresses
      2. Structured JSON hydration blocks (__NEXT_DATA__, application/json)
      3. Visible HTML text (with <script> and <style> stripped to avoid JS noise)
    """
    if not page_html:
        return []

    # 1. Explicit mailto: links + Cloudflare email-protection XOR payloads.
    priority_chunks = [
        _unescape_text(unquote(m)) for m in _MAILTO_RE.findall(page_html)
    ]
    for cf_a, cf_b in _CF_EMAIL_RE.findall(page_html):
        decoded = _decode_cfemail(cf_a or cf_b)
        if decoded:
            priority_chunks.append(decoded)

    # 2. Structured JSON data blocks (Linktree/Bio.site __NEXT_DATA__, JSON-LD)
    #    unescaped so \u0040 -> '@' and \u003e -> '>' before email regex runs.
    json_chunks = [_unescape_text(block) for block in _JSON_SCRIPT_RE.findall(page_html)]

    # 3. Visible HTML with <script>/<style> stripped so minified JS (_0@l.gh)
    #    and font-license comments never leak in.
    visible_html = _unescape_text(_STYLE_OR_SCRIPT_RE.sub(" ", page_html))

    chunks = [*priority_chunks, *json_chunks, visible_html]
    return [email for email in extract_emails(*chunks) if not _is_noise(email)]


def _fetch_html(url: str, *, timeout_seconds: float) -> str:
    with _SESSION.get(
        url,
        timeout=timeout_seconds,
        stream=True,
        allow_redirects=True,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if content_type and "text" not in content_type and "json" not in content_type:
            return ""
        body = response.raw.read(_MAX_RESPONSE_BYTES, decode_content=True)
        return body.decode(response.encoding or "utf-8", errors="replace")


def _emails_from_url(url: str, *, timeout_seconds: float) -> list[str]:
    page_html = _fetch_html(url, timeout_seconds=timeout_seconds)
    return extract_emails_from_html(page_html)


def _decode_cfemail(hex_str: str) -> str | None:
    """Decode Cloudflare's /cdn-cgi/l/email-protection XOR-obfuscated hex string."""
    if len(hex_str) < 4 or len(hex_str) % 2 != 0:
        return None
    try:
        key = int(hex_str[:2], 16)
        return "".join(chr(int(hex_str[i : i + 2], 16) ^ key) for i in range(2, len(hex_str), 2))
    except ValueError:
        return None


def _unescape_text(text: str) -> str:
    text = _JSON_UNICODE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return html.unescape(text)


def _is_noise(email: str) -> bool:
    local, sep, domain = email.partition("@")
    if not sep or not local or not domain:
        return True
    if email.endswith(_ASSET_SUFFIXES):
        return True
    if local in _NOISE_LOCAL_PARTS or _HEX32_RE.match(local) or _UNICODE_PREFIX_RE.match(local):
        return True
    # Reject minified-JS artifacts like "_0@l.gh" (leading underscore or 1-char host label).
    if local.startswith("_") or any(len(label) < 2 for label in domain.split(".")):
        return True
    return any(domain == d or domain.endswith(f".{d}") for d in _PLATFORM_EMAIL_DOMAINS)


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _is_link_service(url: str) -> bool:
    return _host_of(url) in LINK_SERVICE_DOMAINS


def _is_skipped_host(url: str) -> bool:
    host = _host_of(url)
    return any(host == d or host.endswith(f".{d}") for d in _SKIP_FETCH_DOMAINS)


def _normalize_url(raw: str) -> str | None:
    raw = (raw or "").strip().strip(".,;:!?)([]'\"")
    if not raw:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"https://{raw}"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if "." not in hostname:
        return None
    # Upgrade http:// -> https:// on known HTTPS link-in-bio services (avoids port-80 timeouts).
    scheme = parsed.scheme.lower()
    if scheme == "http" and hostname.removeprefix("www.") in LINK_SERVICE_DOMAINS:
        scheme = "https"
    netloc = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))
