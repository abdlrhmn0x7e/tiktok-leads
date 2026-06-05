from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class CandidateProfile:
    handle: str
    profile_url: str
    niche: str
    followers_count: int | None = None
    recent_video_views: list[int] = field(default_factory=list)
    bio: str = ""
    external_links: list[str] = field(default_factory=list)
    source: str = "unknown"

    @property
    def average_views(self) -> int | None:
        if not self.recent_video_views:
            return None
        return round(sum(self.recent_video_views) / len(self.recent_video_views))


@dataclass(frozen=True)
class Lead:
    handle: str
    profile_url: str
    niche: str
    email: str
    followers_count: int
    average_views: int
    source: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
