from __future__ import annotations

from tiktok_leads.settings import Settings
from tiktok_leads.sources import TikTokApiSource


def build_proxy(settings: Settings) -> dict[str, str] | None:
    if not settings.proxy_server:
        return None
    proxy = {"server": normalize_proxy_server(settings.proxy_server)}
    if settings.proxy_username:
        proxy["username"] = settings.proxy_username
    if settings.proxy_password:
        proxy["password"] = settings.proxy_password
    return proxy


def normalize_proxy_server(proxy_server: str) -> str:
    proxy_server = proxy_server.strip()
    if "://" in proxy_server:
        return proxy_server
    return f"http://{proxy_server}"


def build_source(settings: Settings) -> TikTokApiSource:
    return TikTokApiSource(
        ms_token=settings.tiktok_ms_token,
        recent_video_count=settings.effective_recent_video_count,
        min_followers=settings.min_followers,
        skip_videos_without_email=settings.tiktok_skip_videos_without_email,
        skip_videos_below_min_followers=settings.tiktok_skip_videos_below_min_followers,
        headless=settings.tiktok_browser_headless,
        browser=settings.tiktok_browser,
        starting_url=settings.tiktok_starting_url,
        session_timeout_ms=settings.tiktok_session_timeout_ms,
        session_retries=settings.tiktok_session_retries,
        request_delay_seconds=settings.effective_request_delay_seconds,
        request_jitter_seconds=settings.effective_request_jitter_seconds,
        max_consecutive_blocked_profiles=settings.tiktok_max_consecutive_blocked_profiles,
        block_cooldown_seconds=settings.tiktok_block_cooldown_seconds,
        max_block_cooldowns_per_hashtag=settings.tiktok_max_block_cooldowns_per_hashtag,
        restart_session_on_block=settings.tiktok_restart_session_on_block,
        restart_session_between_hashtags=settings.tiktok_restart_session_between_hashtags,
        proxy=build_proxy(settings),
    )
