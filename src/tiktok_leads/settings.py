from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_path: Path = Path("data/leads.sqlite")
    min_followers: int = 10_000
    min_average_views: int = 10_000
    notification_channel: str = "none"
    discord_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Primary scraping controls.
    scrape_mode: Literal["polite", "balanced", "aggressive"] = "polite"
    scrape_profile_delay_seconds: float = 8.0
    scrape_profile_jitter_seconds: float = 4.0
    scrape_burst_size: int = 1
    scrape_rest_seconds: float = 7_200.0
    scrape_rest_jitter_seconds: float = 1_800.0
    scrape_recent_video_count: int = 6
    discovery_search: bool = False

    # TikTok session settings.
    tiktok_ms_token: str | None = None
    tiktok_browser_headless: bool = True
    tiktok_browser: str = "chromium"
    tiktok_starting_url: str = "https://www.tiktok.com"
    tiktok_session_timeout_ms: int = 90_000
    tiktok_session_retries: int = 2

    # Lower-level compatibility knobs. Prefer the SCRAPE_* settings above.
    recent_video_count: int = 12
    tiktok_request_delay_seconds: float = 2.0
    tiktok_request_jitter_seconds: float = 1.5
    tiktok_skip_videos_without_email: bool = True
    tiktok_skip_videos_below_min_followers: bool = True
    tiktok_max_consecutive_blocked_profiles: int = 5
    tiktok_block_cooldown_seconds: float = 10.0
    tiktok_max_block_cooldowns_per_hashtag: int = 1
    tiktok_restart_session_on_block: bool = True
    tiktok_restart_session_between_hashtags: bool = True
    tiktok_suppress_library_errors: bool = True
    shuffle_hashtags: bool = True

    # Daemon mode: run forever, scrape a small slice each cycle, ride out blocks.
    daemon_hashtags_per_cycle: int = 3
    daemon_cycle_sleep_seconds: float = 21_600.0  # ~6h rest between cycles
    daemon_cycle_jitter_seconds: float = 1_800.0  # up to +30m randomness
    daemon_error_cooldown_seconds: float = 120.0  # pause after an unexpected error
    daemon_block_backoff_initial_seconds: float = 1_800.0  # first block backoff: 30m
    daemon_block_backoff_max_seconds: float = 21_600.0  # cap backoff at 6h
    daemon_block_backoff_multiplier: float = 2.0  # exponential growth per repeated block
    daemon_block_backoff_jitter_seconds: float = 600.0  # up to +10m randomness

    # Discovery expansion (lead-volume features).
    discovery_use_harvested_hashtags: bool = True  # crawl hashtags found in scraped videos
    discovery_harvested_hashtags_per_niche: int = 15  # cap added per sweep
    discovery_harvested_min_mentions: int = 2  # only crawl tags seen at least this often
    discovery_harvested_recrawl_after_days: int = 7  # don't re-crawl a harvested tag sooner
    discovery_use_search: bool = True  # crawl api.search.users for niche phrases
    discovery_search_results_per_term: int = 20  # users requested per search phrase
    recheck_enabled: bool = True  # re-check stale profiles for newly-added emails
    recheck_after_days: int = 21  # a profile is "stale" once unseen this long
    recheck_handles_per_sweep: int = 10  # stale handles to re-check each sweep

    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None

    @property
    def effective_recent_video_count(self) -> int:
        return self.scrape_recent_video_count

    @property
    def effective_request_delay_seconds(self) -> float:
        return self.scrape_profile_delay_seconds

    @property
    def effective_request_jitter_seconds(self) -> float:
        return self.scrape_profile_jitter_seconds

    @property
    def effective_burst_size(self) -> int:
        burst_size = max(1, self.scrape_burst_size)
        if self.scrape_mode == "balanced":
            return max(burst_size, 2)
        if self.scrape_mode == "aggressive":
            return max(burst_size, 3)
        return burst_size

    @property
    def effective_rest_seconds(self) -> float:
        if self.scrape_mode == "balanced" and self.scrape_rest_seconds == 7_200.0:
            return 3_600.0
        if self.scrape_mode == "aggressive" and self.scrape_rest_seconds == 7_200.0:
            return 1_800.0
        return self.scrape_rest_seconds

    @property
    def effective_rest_jitter_seconds(self) -> float:
        return self.scrape_rest_jitter_seconds

    @property
    def effective_discovery_search(self) -> bool:
        if self.scrape_mode in {"balanced", "aggressive"}:
            return True
        return self.discovery_search
