from __future__ import annotations

import logging
from urllib.parse import unquote, urlsplit

from proxyproviders import ProxyConfig, ProxyProvider, Webshare
from proxyproviders.models.proxy import Proxy

from tiktok_leads.settings import Settings
from tiktok_leads.sources import TikTokApiSource

logger = logging.getLogger(__name__)

# Auto session count is capped: each session is a full browser context, and a
# home machine gets diminishing returns past a handful of them.
MAX_AUTO_SESSIONS = 5


class StaticProxyProvider(ProxyProvider):
    """Serves a fixed proxy list. TikTokApi assigns proxies to sessions through
    the provider's round-robin algorithm, so N proxies -> one per session."""

    def __init__(self, proxies: list[Proxy]) -> None:
        super().__init__(ProxyConfig(refresh_interval=0))
        self._static_proxies = proxies

    def _fetch_proxies(self) -> list[Proxy]:
        return self._static_proxies


def normalize_proxy_server(proxy_server: str) -> str:
    proxy_server = proxy_server.strip()
    if "://" in proxy_server:
        return proxy_server
    return f"http://{proxy_server}"


def parse_proxy(server: str, index: int, settings: Settings) -> Proxy:
    """Parse 'host:port', 'scheme://host:port', or 'scheme://user:pass@host:port'.
    Entries without inline credentials fall back to PROXY_USERNAME/PROXY_PASSWORD."""
    parsed = urlsplit(normalize_proxy_server(server))
    try:
        port = parsed.port
    except ValueError:
        port = None
    if not parsed.hostname or port is None:
        raise ValueError(
            f"proxy entry {server!r} is not valid: expected host:port "
            "(optionally with scheme and credentials, e.g. http://user:pass@host:port)"
        )
    username = unquote(parsed.username) if parsed.username else (settings.proxy_username or "")
    password = unquote(parsed.password) if parsed.password else (settings.proxy_password or "")
    return Proxy(
        id=f"static-{index}",
        username=username,
        password=password,
        proxy_address=parsed.hostname,
        port=port,
        protocols=[parsed.scheme] if parsed.scheme else None,
    )


def build_proxy_provider(settings: Settings) -> ProxyProvider | None:
    if settings.webshare_api_key:
        return Webshare(api_key=settings.webshare_api_key)
    servers = settings.effective_proxy_servers
    if not servers:
        return None
    return StaticProxyProvider([parse_proxy(server, i, settings) for i, server in enumerate(servers)])


def resolve_num_sessions(settings: Settings, proxy_provider: ProxyProvider | None) -> int:
    if settings.tiktok_num_sessions > 0:
        return settings.tiktok_num_sessions
    if proxy_provider is None:
        return 1
    try:
        proxy_count = len(proxy_provider.list_proxies())
    except Exception:
        logger.exception("could not list proxies to size sessions; falling back to 1")
        return 1
    return max(1, min(proxy_count, MAX_AUTO_SESSIONS))


def build_source(settings: Settings) -> TikTokApiSource:
    proxy_provider = build_proxy_provider(settings)
    return TikTokApiSource(
        ms_tokens=settings.effective_ms_tokens,
        num_sessions=resolve_num_sessions(settings, proxy_provider),
        recent_video_count=settings.effective_recent_video_count,
        min_followers=settings.min_followers,
        skip_videos_without_email=settings.tiktok_skip_videos_without_email,
        skip_videos_below_min_followers=settings.tiktok_skip_videos_below_min_followers,
        headless=settings.tiktok_browser_headless,
        browser=settings.tiktok_browser,
        starting_url=settings.tiktok_starting_url,
        session_timeout_ms=settings.tiktok_session_timeout_ms,
        session_retries=settings.tiktok_session_retries,
        suppress_resource_load_types=settings.effective_suppress_resource_types,
        request_delay_seconds=settings.effective_request_delay_seconds,
        request_jitter_seconds=settings.effective_request_jitter_seconds,
        max_consecutive_blocked_profiles=settings.tiktok_max_consecutive_blocked_profiles,
        block_cooldown_seconds=settings.tiktok_block_cooldown_seconds,
        max_block_cooldowns_per_hashtag=settings.tiktok_max_block_cooldowns_per_hashtag,
        restart_session_on_block=settings.tiktok_restart_session_on_block,
        restart_session_between_hashtags=settings.tiktok_restart_session_between_hashtags,
        proxy_provider=proxy_provider,
        resolve_bio_link_emails=settings.resolve_bio_link_emails,
        bio_link_timeout_seconds=settings.bio_link_timeout_seconds,
        bio_link_max_fetches=settings.bio_link_max_fetches,
    )
