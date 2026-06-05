from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_path: Path = Path("data/leads.sqlite")
    min_followers: int = 10_000
    min_average_views: int = 10_000
    recent_video_count: int = 12
    notification_channel: str = "none"
    discord_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    tiktok_ms_token: str | None = None
    tiktok_browser_headless: bool = True
    tiktok_browser: str = "chromium"
    tiktok_starting_url: str = "https://www.tiktok.com"
    tiktok_session_timeout_ms: int = 90_000
    tiktok_session_retries: int = 2
    tiktok_request_delay_seconds: float = 2.0
    tiktok_request_jitter_seconds: float = 1.5
    tiktok_max_consecutive_blocked_profiles: int = 5
    tiktok_block_cooldown_seconds: float = 10.0
    tiktok_max_block_cooldowns_per_hashtag: int = 1
    tiktok_restart_session_on_block: bool = True
    tiktok_restart_session_between_hashtags: bool = True
    tiktok_suppress_library_errors: bool = True
    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
