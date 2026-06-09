from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, Iterable

from tiktok_leads.models import CandidateProfile


class TikTokSource(ABC):
    @abstractmethod
    def profiles_from_handles(
        self,
        handles: Iterable[str],
        *,
        niche: str,
    ) -> AsyncIterable[CandidateProfile]:
        raise NotImplementedError

    @abstractmethod
    def profiles_from_hashtag(
        self,
        hashtag: str,
        *,
        niche: str,
        limit: int,
        exclude_handles: set[str] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        raise NotImplementedError

    @abstractmethod
    def profiles_from_search(
        self,
        query: str,
        *,
        niche: str,
        limit: int,
        exclude_handles: set[str] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        raise NotImplementedError
