from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterable, Iterable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any

from TikTokApi import TikTokApi
from TikTokApi.exceptions import EmptyResponseException

from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.models import CandidateProfile
from tiktok_leads.sources.base import TikTokSource

logger = logging.getLogger(__name__)


class ProfileUnavailableError(RuntimeError):
    pass


class TikTokBlockedError(RuntimeError):
    pass


class TikTokApiSource(TikTokSource, AbstractAsyncContextManager["TikTokApiSource"]):
    def __init__(
        self,
        *,
        ms_token: str | None,
        recent_video_count: int,
        min_followers: int,
        skip_videos_without_email: bool,
        skip_videos_below_min_followers: bool,
        headless: bool,
        browser: str,
        starting_url: str,
        session_timeout_ms: int,
        session_retries: int,
        request_delay_seconds: float,
        request_jitter_seconds: float,
        max_consecutive_blocked_profiles: int,
        block_cooldown_seconds: float,
        max_block_cooldowns_per_hashtag: int,
        restart_session_on_block: bool,
        restart_session_between_hashtags: bool,
        proxy: dict[str, str] | None,
    ) -> None:
        self.ms_token = ms_token
        self.recent_video_count = recent_video_count
        self.min_followers = min_followers
        self.skip_videos_without_email = skip_videos_without_email
        self.skip_videos_below_min_followers = skip_videos_below_min_followers
        self.headless = headless
        self.browser = browser
        self.starting_url = starting_url
        self.session_timeout_ms = session_timeout_ms
        self.session_retries = session_retries
        self.request_delay_seconds = request_delay_seconds
        self.request_jitter_seconds = request_jitter_seconds
        self.max_consecutive_blocked_profiles = max_consecutive_blocked_profiles
        self.block_cooldown_seconds = block_cooldown_seconds
        self.max_block_cooldowns_per_hashtag = max_block_cooldowns_per_hashtag
        self.restart_session_on_block = restart_session_on_block
        self.restart_session_between_hashtags = restart_session_between_hashtags
        self.proxy = proxy
        self.api: TikTokApi | None = None

    async def __aenter__(self) -> "TikTokApiSource":
        await self._start_session()
        return self

    async def _start_session(self) -> None:
        last_error: BaseException | None = None
        for attempt in range(1, self.session_retries + 2):
            self.api = TikTokApi()
            logger.info(
                "starting TikTokApi session attempt=%s browser=%s headless=%s timeout_ms=%s ms_token=%s",
                attempt,
                self.browser,
                self.headless,
                self.session_timeout_ms,
                "configured" if self.ms_token else "not configured",
            )
            if self.proxy:
                logger.info("using proxy server=%s", self.proxy.get("server"))
            try:
                await self.api.create_sessions(
                    ms_tokens=[self.ms_token] if self.ms_token else None,
                    num_sessions=1,
                    proxies=[self.proxy] if self.proxy else None,
                    sleep_after=3,
                    browser=self.browser,
                    headless=self.headless,
                    starting_url=self.starting_url,
                    timeout=self.session_timeout_ms,
                )
                return
            except Exception as error:
                last_error = error
                logger.warning("failed to create TikTokApi session attempt=%s: %s", attempt, error)
                await self._close_session()
                if attempt <= self.session_retries:
                    await asyncio.sleep(self.block_cooldown_seconds)
        assert last_error is not None
        raise last_error

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._close_session()

    async def _close_session(self) -> None:
        if self.api is not None:
            try:
                await self.api.close_sessions()
                stop_playwright = getattr(self.api, "stop_playwright", None)
                if stop_playwright is not None:
                    await stop_playwright()
            except Exception:
                logger.exception("failed to close TikTokApi session cleanly")
            self.api = None

    async def _restart_session(self) -> None:
        logger.info("restarting TikTokApi Playwright session")
        await self._close_session()
        if self.block_cooldown_seconds > 0:
            await asyncio.sleep(self.block_cooldown_seconds)
        await self._start_session()

    async def profiles_from_handles(
        self,
        handles: Iterable[str],
        *,
        niche: str,
    ) -> AsyncIterable[CandidateProfile]:
        consecutive_blocked = 0
        cooldowns = 0
        for handle in handles:
            api = self._api()
            username = handle.removeprefix("@").strip()
            if not username:
                continue
            profile = await self._safe_profile_from_user(
                api.user(username=username),
                niche=niche,
                handle=username,
            )
            if profile is not None:
                consecutive_blocked = 0
                yield profile
            else:
                consecutive_blocked += 1
                if await self._recover_if_blocked(consecutive_blocked, context="handle scan"):
                    consecutive_blocked = 0
                    cooldowns += 1
                    if cooldowns > self.max_block_cooldowns_per_hashtag:
                        raise TikTokBlockedError("repeated TikTok blocking during handle scan")
            await self._delay()

    async def profiles_from_hashtag(
        self,
        hashtag: str,
        *,
        niche: str,
        limit: int,
        exclude_handles: set[str] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        api = self._api()
        tag = hashtag.removeprefix("#").strip()
        excluded = {handle.removeprefix("@").lower() for handle in exclude_handles or set()}
        seen_handles: set[str] = set()
        consecutive_blocked = 0
        blocked = False
        try:
            async for video in api.hashtag(name=tag).videos(count=limit):
                author = self._get(video.as_dict, "author", default={})
                handle = str(self._get(author, "uniqueId", "unique_id", "nickname", default="")).strip()
                normalized_handle = handle.removeprefix("@").lower()
                if not handle or normalized_handle in seen_handles:
                    continue
                if normalized_handle in excluded:
                    logger.info("skipping @%s from #%s: handle already evaluated", handle, tag)
                    continue
                seen_handles.add(normalized_handle)
                profile = await self._safe_profile_from_user(
                    api.user(username=handle),
                    niche=niche,
                    handle=handle,
                    context=f"from #{tag}",
                )
                if profile is not None:
                    consecutive_blocked = 0
                    yield profile
                else:
                    consecutive_blocked += 1
                    if await self._recover_if_blocked(consecutive_blocked, context=f"#{tag}"):
                        blocked = True
                        raise TikTokBlockedError(f"sustained TikTok blocking on #{tag}")
                await self._delay()
        except TikTokBlockedError:
            raise
        except Exception:
            logger.exception("failed to crawl hashtag #%s", tag)
        finally:
            # Don't spin up a fresh session if we're bailing out blocked — the
            # daemon will back off and reopen the source on the next cycle.
            if self.restart_session_between_hashtags and not blocked:
                await self._restart_session()

    async def profiles_from_search(
        self,
        query: str,
        *,
        niche: str,
        limit: int,
        exclude_handles: set[str] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        api = self._api()
        excluded = {handle.removeprefix("@").lower() for handle in exclude_handles or set()}
        seen_handles: set[str] = set()
        consecutive_blocked = 0
        blocked = False
        try:
            async for user in api.search.users(query, count=limit):
                handle = self._handle_from_user(user)
                normalized_handle = handle.removeprefix("@").lower()
                if not handle or normalized_handle in seen_handles:
                    continue
                if normalized_handle in excluded:
                    logger.info("skipping @%s from search '%s': handle already evaluated", handle, query)
                    continue
                seen_handles.add(normalized_handle)
                profile = await self._safe_profile_from_user(
                    api.user(username=handle),
                    niche=niche,
                    handle=handle,
                    context=f"from search '{query}'",
                )
                if profile is not None:
                    consecutive_blocked = 0
                    yield profile
                else:
                    consecutive_blocked += 1
                    if await self._recover_if_blocked(consecutive_blocked, context=f"search '{query}'"):
                        blocked = True
                        raise TikTokBlockedError(f"sustained TikTok blocking on search '{query}'")
                await self._delay()
        except TikTokBlockedError:
            raise
        except Exception:
            logger.exception("failed to search users for '%s'", query)
        finally:
            if self.restart_session_between_hashtags and not blocked:
                await self._restart_session()

    def _handle_from_user(self, user: Any) -> str:
        username = getattr(user, "username", None)
        if username:
            return str(username).strip()
        data = getattr(user, "as_dict", {}) or {}
        user_info = self._get(data, "user_info", "userInfo", default=data)
        nested = self._get(user_info, "user", default=user_info)
        return str(self._get(nested, "uniqueId", "unique_id", "unique_identifier", default="")).strip()

    async def _safe_profile_from_user(
        self,
        user: Any,
        *,
        niche: str,
        handle: str,
        context: str = "",
    ) -> CandidateProfile | None:
        try:
            return await self._profile_from_user(user, niche=niche)
        except (EmptyResponseException, KeyError, ProfileUnavailableError) as error:
            suffix = f" {context}" if context else ""
            logger.warning("skipped @%s%s: TikTok returned an empty/blocked profile (%s)", handle, suffix, error)
            return None
        except Exception:
            suffix = f" {context}" if context else ""
            logger.exception("failed to fetch profile @%s%s", handle, suffix)
            return None

    async def _profile_from_user(self, user: Any, *, niche: str) -> CandidateProfile:
        info = await user.info()
        user_info = self._get(info, "userInfo", default=info)
        user_data = self._get(user_info, "user", default={})
        stats = self._get(user_info, "stats", "statsV2", default={})
        if not isinstance(user_data, dict) or not user_data.get("id"):
            raise ProfileUnavailableError("missing userInfo.user.id")

        handle = str(self._get(user_data, "uniqueId", "unique_id", default="")).strip()
        if not handle and hasattr(user, "username"):
            handle = str(user.username).strip()

        bio = str(self._get(user_data, "signature", default="") or "")
        followers = self._parse_int(self._get(stats, "followerCount", "follower_count", default=None))
        links = self._extract_links(user_data)

        if (
            self.skip_videos_below_min_followers
            and followers is not None
            and followers < self.min_followers
        ):
            logger.info(
                "skipping recent videos for @%s: followers below threshold (%s < %s)",
                handle,
                followers,
                self.min_followers,
            )
            return CandidateProfile(
                handle=handle,
                profile_url=f"https://www.tiktok.com/@{handle}",
                niche=niche,
                followers_count=followers,
                bio=bio,
                external_links=links,
                source="tiktokapi",
            )

        if self.skip_videos_without_email and not extract_emails(bio, *links):
            logger.info("skipping recent videos for @%s: no public email in profile", handle)
            return CandidateProfile(
                handle=handle,
                profile_url=f"https://www.tiktok.com/@{handle}",
                niche=niche,
                followers_count=followers,
                bio=bio,
                external_links=links,
                source="tiktokapi",
            )

        views: list[int] = []
        hashtags: set[str] = set()
        async for video in user.videos(count=self.recent_video_count):
            video_data = video.as_dict
            video_stats = self._get(video_data, "stats", default={})
            play_count = self._parse_int(self._get(video_stats, "playCount", "play_count", default=None))
            if play_count is not None:
                views.append(play_count)
            hashtags.update(self._extract_hashtags(video_data))

        return CandidateProfile(
            handle=handle,
            profile_url=f"https://www.tiktok.com/@{handle}",
            niche=niche,
            followers_count=followers,
            recent_video_views=views,
            bio=bio,
            external_links=links,
            source="tiktokapi",
            discovered_hashtags=sorted(hashtags),
        )

    async def _delay(self) -> None:
        delay = self.request_delay_seconds
        if self.request_jitter_seconds > 0:
            delay += random.uniform(0, self.request_jitter_seconds)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _recover_if_blocked(self, consecutive_blocked: int, *, context: str) -> bool:
        if consecutive_blocked < self.max_consecutive_blocked_profiles:
            return False
        if self.restart_session_on_block:
            logger.warning(
                "hit %s consecutive blocked/empty profiles during %s; restarting session",
                consecutive_blocked,
                context,
            )
            await self._restart_session()
        else:
            logger.warning(
                "hit %s consecutive blocked/empty profiles during %s; cooling down for %.0f seconds",
                consecutive_blocked,
                context,
                self.block_cooldown_seconds,
            )
            if self.block_cooldown_seconds > 0:
                await asyncio.sleep(self.block_cooldown_seconds)
        return True

    def _api(self) -> TikTokApi:
        if self.api is None:
            raise RuntimeError("TikTokApiSource must be used as a context manager")
        return self.api

    @staticmethod
    def _get(data: Any, *keys: str, default: Any = None) -> Any:
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                return current[key]
        return default

    def _extract_hashtags(self, video_data: dict[str, Any]) -> set[str]:
        tags: set[str] = set()
        # `textExtra` carries the hashtags actually used in the caption.
        text_extra = self._get(video_data, "textExtra", default=[])
        if isinstance(text_extra, list):
            for item in text_extra:
                if isinstance(item, dict):
                    name = item.get("hashtagName")
                    if name:
                        tags.add(str(name).strip().lower())
        # `challenges` is the structured hashtag list.
        challenges = self._get(video_data, "challenges", default=[])
        if isinstance(challenges, list):
            for item in challenges:
                if isinstance(item, dict):
                    title = item.get("title")
                    if title:
                        tags.add(str(title).strip().lower())
        tags.discard("")
        return tags

    @staticmethod
    def _parse_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _extract_links(self, user_data: dict[str, Any]) -> list[str]:
        links: list[str] = []
        bio_link = self._get(user_data, "bioLink", default={})
        if isinstance(bio_link, dict):
            link = self._get(bio_link, "link", default=None)
            if link:
                links.append(str(link))
        for key in ("ins_id", "youtube_channel_title", "twitter_id"):
            value = self._get(user_data, key, default=None)
            if value:
                links.append(str(value))
        return links
