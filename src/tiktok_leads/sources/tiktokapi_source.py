from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterable, AsyncIterator, Callable, Coroutine, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from TikTokApi import TikTokApi
from TikTokApi.exceptions import (
    CaptchaException,
    EmptyResponseException,
    InvalidResponseException,
    NotFoundException,
)

from tiktok_leads.bio_links import bio_hints_link_page, emails_from_bio_links
from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.models import CandidateProfile
from tiktok_leads.sources.base import TikTokSource

logger = logging.getLogger(__name__)

# Exceptions that mean TikTok is rate-limiting/bot-detecting us, as opposed to
# a profile being deleted, private, or renamed. Only these count toward the
# consecutive-blocked counter — otherwise a run of dead profiles triggers
# session restarts and multi-hour daemon backoffs for nothing.
_BLOCK_EXCEPTIONS = (EmptyResponseException, CaptchaException)

# Consecutive feed-level (hashtag/search listing) block errors before we give
# up the batch. Two in a row distinguishes a real block from one odd/banned tag.
_MAX_FEED_BLOCK_STREAK = 2


class ProfileUnavailableError(RuntimeError):
    pass


class TikTokBlockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Fetch:
    profile: CandidateProfile | None
    blocked: bool = False


class TikTokApiSource(TikTokSource, AbstractAsyncContextManager["TikTokApiSource"]):
    def __init__(
        self,
        *,
        ms_tokens: list[str] | None,
        num_sessions: int,
        recent_video_count: int,
        min_followers: int,
        skip_videos_without_email: bool,
        skip_videos_below_min_followers: bool,
        headless: bool,
        browser: str,
        starting_url: str,
        session_timeout_ms: int,
        session_retries: int,
        suppress_resource_load_types: list[str] | None,
        request_delay_seconds: float,
        request_jitter_seconds: float,
        max_consecutive_blocked_profiles: int,
        block_cooldown_seconds: float,
        max_block_cooldowns_per_hashtag: int,
        restart_session_on_block: bool,
        restart_session_between_hashtags: bool,
        proxy_provider: Any | None,
        resolve_bio_link_emails: bool = True,
        bio_link_timeout_seconds: float = 10.0,
        bio_link_max_fetches: int = 2,
    ) -> None:
        self.ms_tokens = ms_tokens or []
        self.num_sessions = max(1, num_sessions)
        self.recent_video_count = recent_video_count
        self.min_followers = min_followers
        self.skip_videos_without_email = skip_videos_without_email
        self.skip_videos_below_min_followers = skip_videos_below_min_followers
        self.headless = headless
        self.browser = browser
        self.starting_url = starting_url
        self.session_timeout_ms = session_timeout_ms
        self.session_retries = session_retries
        self.suppress_resource_load_types = suppress_resource_load_types or None
        self.request_delay_seconds = request_delay_seconds
        self.request_jitter_seconds = request_jitter_seconds
        self.max_consecutive_blocked_profiles = max_consecutive_blocked_profiles
        self.block_cooldown_seconds = block_cooldown_seconds
        self.max_block_cooldowns_per_hashtag = max_block_cooldowns_per_hashtag
        self.restart_session_on_block = restart_session_on_block
        self.restart_session_between_hashtags = restart_session_between_hashtags
        self.proxy_provider = proxy_provider
        self.resolve_bio_link_emails = resolve_bio_link_emails
        self.bio_link_timeout_seconds = bio_link_timeout_seconds
        self.bio_link_max_fetches = bio_link_max_fetches
        self.api: TikTokApi | None = None
        self._feed_block_streak = 0

    async def __aenter__(self) -> "TikTokApiSource":
        await self._start_session()
        return self

    async def _start_session(self) -> None:
        last_error: BaseException | None = None
        for attempt in range(1, self.session_retries + 2):
            self.api = TikTokApi()
            logger.info(
                "starting TikTokApi session attempt=%s browser=%s headless=%s timeout_ms=%s sessions=%s ms_tokens=%s",
                attempt,
                self.browser,
                self.headless,
                self.session_timeout_ms,
                self.num_sessions,
                len(self.ms_tokens) or "none",
            )
            if self.proxy_provider is not None:
                logger.info("using proxy provider %s", type(self.proxy_provider).__name__)
            try:
                await self.api.create_sessions(
                    ms_tokens=self.ms_tokens or None,
                    num_sessions=self.num_sessions,
                    proxy_provider=self.proxy_provider,
                    sleep_after=3,
                    browser=self.browser,
                    headless=self.headless,
                    starting_url=self.starting_url,
                    timeout=self.session_timeout_ms,
                    suppress_resource_load_types=self.suppress_resource_load_types,
                    allow_partial_sessions=self.num_sessions > 1,
                    min_sessions=1,
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
            result = await self._safe_fetch(
                self._profile_from_user(api.user(username=username), niche=niche),
                handle=username,
            )
            if result.profile is not None:
                consecutive_blocked = 0
                yield result.profile
            elif result.blocked:
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
        start_cursor: int = 0,
        on_cursor: Callable[[int], None] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        api = self._api()
        tag = hashtag.removeprefix("#").strip()
        excluded = {handle.removeprefix("@").lower() for handle in exclude_handles or set()}
        seen_handles: set[str] = set()
        consecutive_blocked = 0
        blocked = False
        try:
            async for item in self._hashtag_feed_items(
                api, tag, limit=limit, cursor=start_cursor, on_cursor=on_cursor
            ):
                self._feed_block_streak = 0
                author = self._get(item, "author", default={})
                handle = str(self._get(author, "uniqueId", "unique_id", "nickname", default="")).strip()
                normalized_handle = handle.removeprefix("@").lower()
                if not handle or normalized_handle in seen_handles:
                    continue
                if normalized_handle in excluded:
                    logger.info("skipping @%s from #%s: handle already evaluated", handle, tag)
                    continue
                seen_handles.add(normalized_handle)

                prefiltered = self._candidate_from_feed_item(item, niche=niche, handle=handle)
                if prefiltered is not None:
                    # Disqualified using data already in the feed — zero requests spent.
                    yield prefiltered
                    await self._feed_skip_delay()
                    continue

                result = await self._fetch_candidate(
                    api, item, niche=niche, handle=handle, context=f"from #{tag}"
                )
                if result.profile is not None:
                    consecutive_blocked = 0
                    yield result.profile
                elif result.blocked:
                    consecutive_blocked += 1
                    if await self._recover_if_blocked(consecutive_blocked, context=f"#{tag}"):
                        blocked = True
                        raise TikTokBlockedError(f"sustained TikTok blocking on #{tag}")
                await self._delay()
        except TikTokBlockedError:
            raise
        except Exception as error:
            blocked = await self._handle_feed_error(error, context=f"#{tag}")
            if blocked:
                raise TikTokBlockedError(f"feed requests blocked on #{tag}") from error
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
            async for user_info in self._search_user_items(api, query, limit=limit):
                self._feed_block_streak = 0
                handle = str(self._get(user_info, "unique_id", "uniqueId", default="")).strip()
                normalized_handle = handle.removeprefix("@").lower()
                if not handle or normalized_handle in seen_handles:
                    continue
                if normalized_handle in excluded:
                    logger.info("skipping @%s from search '%s': handle already evaluated", handle, query)
                    continue
                seen_handles.add(normalized_handle)

                followers = self._parse_int(
                    self._get(user_info, "follower_count", "followerCount", default=None)
                )
                bio = self._get(user_info, "signature", default=None)
                prefiltered = self._prefiltered_candidate(
                    niche=niche, handle=handle, followers=followers, bio=bio
                )
                if prefiltered is not None:
                    # Disqualified using data already in the search payload —
                    # zero requests spent.
                    yield prefiltered
                    await self._feed_skip_delay()
                    continue

                sec_uid = str(self._get(user_info, "sec_uid", "secUid", default="") or "").strip()
                if followers is not None and bio is not None and sec_uid:
                    fetch = self._profile_from_videos(
                        api.user(username=handle, sec_uid=sec_uid),
                        niche=niche,
                        handle=handle,
                        followers=followers,
                        bio=str(bio),
                        feed_hashtags=set(),
                    )
                else:
                    fetch = self._profile_from_user(api.user(username=handle), niche=niche)
                result = await self._safe_fetch(
                    fetch,
                    handle=handle,
                    context=f"from search '{query}'",
                )
                if result.profile is not None:
                    consecutive_blocked = 0
                    yield result.profile
                elif result.blocked:
                    consecutive_blocked += 1
                    if await self._recover_if_blocked(consecutive_blocked, context=f"search '{query}'"):
                        blocked = True
                        raise TikTokBlockedError(f"sustained TikTok blocking on search '{query}'")
                await self._delay()
        except TikTokBlockedError:
            raise
        except Exception as error:
            blocked = await self._handle_feed_error(error, context=f"search '{query}'")
            if blocked:
                raise TikTokBlockedError(f"feed requests blocked on search '{query}'") from error
        finally:
            if self.restart_session_between_hashtags and not blocked:
                await self._restart_session()

    async def _hashtag_feed_items(
        self,
        api: TikTokApi,
        tag: str,
        *,
        limit: int,
        cursor: int,
        on_cursor: Callable[[int], None] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Same pagination as Hashtag.videos, but starts at an arbitrary cursor
        and reports the next page's cursor after each fully-consumed page (0
        once the feed is exhausted, so the next crawl wraps back to the top)."""
        challenge = api.hashtag(name=tag)
        if getattr(challenge, "id", None) is None:
            await challenge.info()
        found = 0
        while found < limit:
            resp = await api.make_request(
                url="https://www.tiktok.com/api/challenge/item_list/",
                params={"challengeID": challenge.id, "count": 30, "cursor": cursor},
            )
            if resp is None:
                raise InvalidResponseException(resp, "TikTok returned an invalid response.")
            items = resp.get("itemList", []) or []
            for item in items:
                yield item
                found += 1
            has_more = bool(resp.get("hasMore", False)) and bool(items)
            cursor = self._parse_int(resp.get("cursor")) or 0
            if on_cursor is not None:
                on_cursor(cursor if has_more else 0)
            if not has_more:
                return

    async def _search_user_items(
        self,
        api: TikTokApi,
        query: str,
        *,
        limit: int,
    ) -> AsyncIterator[dict[str, Any]]:
        """Same pagination as Search.users, but yields the raw user_info
        payloads — they carry follower_count/signature/sec_uid, which the
        library's User objects drop, and which let us prefilter creators
        without spending a profile request."""
        cursor = 0
        search_id = ""
        found = 0
        while found < limit:
            params: dict[str, Any] = {
                "keyword": query,
                "cursor": cursor,
                "from_page": "search",
                "web_search_code": (
                    '{"tiktok":{"client_params_x":{"search_engine":'
                    '{"ies_mt_user_live_video_card_use_libra":1,'
                    '"mt_search_general_user_live_card":1}},"search_server":{}}}'
                ),
            }
            if search_id:
                params["search_id"] = search_id
            resp = await api.make_request(
                url="https://www.tiktok.com/api/search/user/full/",
                params=params,
            )
            if resp is None:
                raise InvalidResponseException(resp, "TikTok returned an invalid response.")
            for entry in resp.get("user_list", []) or []:
                user_info = entry.get("user_info") if isinstance(entry, dict) else None
                if isinstance(user_info, dict):
                    yield user_info
                    found += 1
            if not resp.get("has_more", False):
                return
            cursor = self._parse_int(resp.get("cursor")) or 0
            search_id = str(resp.get("rid", "") or "")

    async def _handle_feed_error(self, error: Exception, *, context: str) -> bool:
        """Classify a failure of the feed/listing request itself. Returns True
        when the streak says we're genuinely blocked (caller then raises)."""
        if isinstance(error, _BLOCK_EXCEPTIONS):
            self._feed_block_streak += 1
            logger.warning(
                "feed request blocked during %s (streak %s/%s): %s",
                context,
                self._feed_block_streak,
                _MAX_FEED_BLOCK_STREAK,
                error,
            )
            return self._feed_block_streak >= _MAX_FEED_BLOCK_STREAK
        if self._is_dead_session_error(error):
            logger.warning("all sessions died during %s; restarting", context)
            await self._restart_session()
            return False
        logger.exception("failed to crawl %s", context)
        return False

    @staticmethod
    def _is_dead_session_error(error: BaseException) -> bool:
        # TikTokApi raises plain Exception when its sessions die and recovery
        # fails, so the message is all there is to match on.
        message = str(error).lower()
        return "no valid sessions" in message or "no sessions created" in message

    def _candidate_from_feed_item(
        self,
        item: dict[str, Any],
        *,
        niche: str,
        handle: str,
    ) -> CandidateProfile | None:
        author = self._get(item, "author", default={})
        if not isinstance(author, dict):
            return None
        stats = self._get(item, "authorStats", "author_stats", default={})
        followers = self._parse_int(self._get(stats, "followerCount", "follower_count", default=None))
        return self._prefiltered_candidate(
            niche=niche,
            handle=handle,
            followers=followers,
            bio=self._get(author, "signature", default=None),
            discovered_hashtags=sorted(self._extract_hashtags(item)),
        )

    def _prefiltered_candidate(
        self,
        *,
        niche: str,
        handle: str,
        followers: int | None,
        bio: Any,
        discovered_hashtags: list[str] | None = None,
    ) -> CandidateProfile | None:
        """Disqualify a creator using only feed/search payload data (zero
        requests). Returns a candidate the filters will reject, or None when
        the creator either passes the prefilters or the payload lacks the data
        to decide."""
        if followers is None:
            return None
        candidate = CandidateProfile(
            handle=handle,
            profile_url=f"https://www.tiktok.com/@{handle}",
            niche=niche,
            followers_count=followers,
            bio=str(bio or ""),
            source="tiktokapi",
            discovered_hashtags=discovered_hashtags or [],
        )
        if self.skip_videos_below_min_followers and followers < self.min_followers:
            return candidate
        if self.skip_videos_without_email and bio is not None and not extract_emails(str(bio)):
            # A bio that references a link page (Linktree etc.) may hide its
            # email one hop away — worth the full fetch to find out. Otherwise
            # the recheck flywheel re-evaluates the profile later.
            if self.resolve_bio_link_emails and bio_hints_link_page(str(bio)):
                return None
            return candidate
        return None

    async def _fetch_candidate(
        self,
        api: TikTokApi,
        item: dict[str, Any],
        *,
        niche: str,
        handle: str,
        context: str,
    ) -> _Fetch:
        """Fetch what the feed couldn't provide. When the feed already gave us
        followers + bio + secUid, only the recent videos are missing — one
        request instead of two."""
        author = self._get(item, "author", default={})
        stats = self._get(item, "authorStats", "author_stats", default={})
        followers = self._parse_int(self._get(stats, "followerCount", "follower_count", default=None))
        bio = self._get(author, "signature", default=None) if isinstance(author, dict) else None
        sec_uid = str(self._get(author, "secUid", "sec_uid", default="") or "").strip()
        if followers is not None and bio is not None and sec_uid:
            return await self._safe_fetch(
                self._profile_from_videos(
                    api.user(username=handle, sec_uid=sec_uid),
                    niche=niche,
                    handle=handle,
                    followers=followers,
                    bio=str(bio),
                    feed_hashtags=self._extract_hashtags(item),
                ),
                handle=handle,
                context=context,
            )
        return await self._safe_fetch(
            self._profile_from_user(api.user(username=handle), niche=niche),
            handle=handle,
            context=context,
        )

    async def _safe_fetch(
        self,
        fetch: Coroutine[Any, Any, CandidateProfile],
        *,
        handle: str,
        context: str = "",
    ) -> _Fetch:
        suffix = f" {context}" if context else ""
        try:
            return _Fetch(await fetch)
        except _BLOCK_EXCEPTIONS as error:
            logger.warning("blocked while fetching @%s%s: %s", handle, suffix, error)
            return _Fetch(None, blocked=True)
        except (NotFoundException, ProfileUnavailableError, KeyError) as error:
            logger.warning("skipped @%s%s: profile unavailable (%s)", handle, suffix, error)
            return _Fetch(None)
        except Exception as error:
            if self._is_dead_session_error(error):
                logger.warning("all sessions died while fetching @%s%s; restarting", handle, suffix)
                await self._restart_session()
                return _Fetch(None)
            logger.exception("failed to fetch profile @%s%s", handle, suffix)
            return _Fetch(None)

    async def _profile_from_videos(
        self,
        user: Any,
        *,
        niche: str,
        handle: str,
        followers: int,
        bio: str,
        feed_hashtags: set[str],
    ) -> CandidateProfile:
        extra_emails: list[str] = []
        if not extract_emails(bio):
            # Reached via the prefilter's link-page hint: the email, if any,
            # lives behind the bio's link. Resolve before paying for videos.
            extra_emails = await self._resolve_bio_link_emails(handle, bio, [])
            if self.skip_videos_without_email and not extra_emails:
                logger.info("skipping recent videos for @%s: no email behind bio link", handle)
                return CandidateProfile(
                    handle=handle,
                    profile_url=f"https://www.tiktok.com/@{handle}",
                    niche=niche,
                    followers_count=followers,
                    bio=bio,
                    external_links=[],
                    source="tiktokapi",
                    discovered_hashtags=sorted(feed_hashtags),
                )
        views, hashtags = await self._collect_recent_videos(user)
        hashtags |= feed_hashtags
        return CandidateProfile(
            handle=handle,
            profile_url=f"https://www.tiktok.com/@{handle}",
            niche=niche,
            followers_count=followers,
            recent_video_views=views,
            bio=bio,
            external_links=[],
            source="tiktokapi",
            discovered_hashtags=sorted(hashtags),
            extra_emails=extra_emails,
        )

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

        extra_emails: list[str] = []
        if not extract_emails(bio, *links):
            extra_emails = await self._resolve_bio_link_emails(handle, bio, links)
            if self.skip_videos_without_email and not extra_emails:
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

        views, hashtags = await self._collect_recent_videos(user)
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
            extra_emails=extra_emails,
        )

    async def _resolve_bio_link_emails(self, handle: str, bio: str, links: list[str]) -> list[str]:
        if not self.resolve_bio_link_emails:
            return []
        emails = await asyncio.to_thread(
            emails_from_bio_links,
            bio,
            links,
            timeout_seconds=self.bio_link_timeout_seconds,
            max_fetches=self.bio_link_max_fetches,
        )
        if emails:
            logger.info("found email via bio link for @%s: %s", handle, emails[0])
        return emails

    async def _collect_recent_videos(self, user: Any) -> tuple[list[int], set[str]]:
        views: list[int] = []
        hashtags: set[str] = set()
        async for video in user.videos(count=self.recent_video_count):
            video_data = video.as_dict
            video_stats = self._get(video_data, "stats", default={})
            play_count = self._parse_int(self._get(video_stats, "playCount", "play_count", default=None))
            if play_count is not None:
                views.append(play_count)
            hashtags.update(self._extract_hashtags(video_data))
        return views, hashtags

    async def _delay(self) -> None:
        delay = self.request_delay_seconds
        if self.request_jitter_seconds > 0:
            delay += random.uniform(0, self.request_jitter_seconds)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _feed_skip_delay(self) -> None:
        # A prefiltered skip made no requests; a token pause just keeps feed
        # pagination from firing back-to-back when a whole page gets skipped.
        await asyncio.sleep(random.uniform(0.2, 0.8))

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
