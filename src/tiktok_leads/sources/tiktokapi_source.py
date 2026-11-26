from __future__ import annotations

import asyncio
import logging
import random
import re
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

from tiktok_leads.bio_links import (
    bio_hints_link_page,
    emails_from_bio_links,
    probe_handle_bio_links,
)
from tiktok_leads.db import FollowingState
from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.models import CandidateProfile
from tiktok_leads.niches import bio_on_niche, is_on_niche, rejected_group, seed_bio_on_niche
from tiktok_leads.numbers import floor_view_count, robust_average_view_count
from tiktok_leads.sources.base import TikTokSource
from tiktok_leads.sources.embed import fetch_embed, fetch_sec_uid

logger = logging.getLogger(__name__)

_CAPTION_HASHTAG = re.compile(r"#(\w+)")

# A hashtag crawl gives up after this many raw feed items per requested new
# candidate, so a feed whose every author is already evaluated still ends.
MAX_FEED_ITEMS_PER_NEW_CANDIDATE = 5


class _Counter:
    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = 0

# Exceptions that mean TikTok is rate-limiting/bot-detecting us, as opposed to
# a profile being deleted, private, or renamed. These and unexplained failures
# count toward the consecutive-blocked counter; dead profiles do not, or a run
# of them triggers session restarts and multi-hour daemon backoffs for nothing.
_BLOCK_EXCEPTIONS = (EmptyResponseException, CaptchaException)

# Consecutive feed-level (hashtag/search listing) block errors before we give
# up the batch. Two in a row distinguishes a real block from one odd/banned tag.
_MAX_FEED_BLOCK_STREAK = 2

# The following-list API's statusCode when the creator hides who they follow
# (about 2 in 3 creators on 2026-11-16).
_FOLLOWING_LIST_HIDDEN = 10222


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
        max_followers: int | None = None,
        min_average_views: int = 0,
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
        embed_delay_seconds: float = 0.5,
        embed_jitter_seconds: float = 0.3,
        following_early_stop_pages: int = 2,
        following_min_on_niche_per_stop: int = 2,
        max_consecutive_blocked_profiles: int,
        block_cooldown_seconds: float,
        restart_session_on_block: bool,
        restart_session_between_hashtags: bool,
        proxy_provider: Any | None,
        resolve_bio_link_emails: bool = True,
        probe_no_email_bio_links: bool = True,
        fetch_bio_link_via_detail: bool = True,
        bio_link_timeout_seconds: float = 10.0,
        bio_link_max_fetches: int = 2,
    ) -> None:
        self.ms_tokens = ms_tokens or []
        self.num_sessions = max(1, num_sessions)
        self.recent_video_count = recent_video_count
        self.min_followers = min_followers
        self.max_followers = max_followers
        self.min_average_views = min_average_views
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
        self.embed_delay_seconds = embed_delay_seconds
        self.embed_jitter_seconds = embed_jitter_seconds
        self.following_early_stop_pages = following_early_stop_pages
        self.following_min_on_niche_per_stop = following_min_on_niche_per_stop
        self.max_consecutive_blocked_profiles = max_consecutive_blocked_profiles
        self.block_cooldown_seconds = block_cooldown_seconds
        self.restart_session_on_block = restart_session_on_block
        self.restart_session_between_hashtags = restart_session_between_hashtags
        self.proxy_provider = proxy_provider
        self.resolve_bio_link_emails = resolve_bio_link_emails
        self.probe_no_email_bio_links = probe_no_email_bio_links
        self.fetch_bio_link_via_detail = fetch_bio_link_via_detail
        self.bio_link_timeout_seconds = bio_link_timeout_seconds
        self.bio_link_max_fetches = bio_link_max_fetches
        self.api: TikTokApi | None = None
        self._feed_block_streak = 0
        # Kept across calls: the daemon rechecks one handle per call.
        self._handle_block_streak = 0
        # Signed Playwright API requests made over this source's life; the daemon
        # paces its rest by how many a cycle made. Unsigned embed fetches are
        # tracked separately so they don't inflate Playwright cycle rest.
        self.request_count = 0
        self.embed_request_count = 0

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
        for handle in handles:
            username = handle.removeprefix("@").strip()
            if not username:
                continue
            result = await self._safe_fetch(
                self._profile_from_user(username, niche=niche),
                handle=username,
            )
            if result.profile is not None:
                self._handle_block_streak = 0
                yield result.profile
            elif result.blocked:
                self._handle_block_streak += 1
                if self._handle_block_streak >= self.max_consecutive_blocked_profiles:
                    self._handle_block_streak = 0
                    raise TikTokBlockedError(
                        f"{self.max_consecutive_blocked_profiles} profile checks in a row failed"
                    )
            await self._embed_delay()

    async def profiles_from_hashtag(
        self,
        hashtag: str,
        *,
        niche: str,
        limit: int,
        exclude_handles: set[str] | None = None,
        start_cursor: int = 0,
        on_cursor: Callable[[int], None] | None = None,
        on_known: Callable[[str], None] | None = None,
        on_finished: Callable[[int], None] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        """`on_finished` gets the number of not-yet-evaluated creators the
        crawl found, and only when the feed ran without a feed error."""
        api = self._api()
        tag = hashtag.removeprefix("#").strip()
        excluded = self._normalize_excluded(exclude_handles)
        seen_handles: set[str] = set()
        consecutive_blocked = 0
        blocked = False
        try:
            # `limit` counts creators we actually evaluate. Already-evaluated
            # handles are free (no request) and don't consume it — otherwise a
            # saturated feed head would exhaust the budget on nothing new.
            inspected = _Counter()
            async for item in self._hashtag_feed_items(
                api, tag, limit=limit, cursor=start_cursor, on_cursor=on_cursor, inspected=inspected
            ):
                self._feed_block_streak = 0
                author = self._get(item, "author", default={})
                handle = str(self._get(author, "uniqueId", "unique_id", "nickname", default="")).strip()
                normalized_handle = handle.removeprefix("@").lower()
                if not handle or normalized_handle in seen_handles:
                    continue
                if normalized_handle in excluded:
                    logger.info("skipping @%s from #%s: handle already evaluated", handle, tag)
                    if on_known is not None:
                        on_known(normalized_handle)
                    continue
                seen_handles.add(normalized_handle)
                inspected.value += 1

                prefiltered = self._candidate_from_feed_item(item, niche=niche, handle=handle)
                if prefiltered is not None:
                    # Disqualified using data already in the feed — zero requests spent.
                    yield prefiltered
                    continue

                result = await self._fetch_candidate(item, niche=niche, handle=handle, context=f"from #{tag}")
                if result.profile is not None:
                    consecutive_blocked = 0
                    yield result.profile
                elif result.blocked:
                    consecutive_blocked += 1
                    if await self._recover_if_blocked(consecutive_blocked, context=f"#{tag}"):
                        blocked = True
                        raise TikTokBlockedError(f"sustained TikTok blocking on #{tag}")
                await self._embed_delay()
            if on_finished is not None:
                on_finished(len(seen_handles))
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
        on_known: Callable[[str], None] | None = None,
        on_finished: Callable[[int], None] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        """`on_finished` gets the number of not-yet-evaluated creators the
        search returned, and only when the listing ran without a feed error."""
        api = self._api()
        excluded = self._normalize_excluded(exclude_handles)
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
                    if on_known is not None:
                        on_known(normalized_handle)
                    continue
                seen_handles.add(normalized_handle)

                followers = self._parse_int(
                    self._get(user_info, "follower_count", "followerCount", default=None)
                )
                bio = self._get(user_info, "signature", default=None)
                external_links = self._extract_bio_links(user_info)
                prefiltered = self._prefiltered_candidate(
                    niche=niche,
                    handle=handle,
                    followers=followers,
                    bio=bio,
                    external_links=external_links,
                )
                if prefiltered is not None:
                    # Disqualified using data already in the search payload —
                    # zero requests spent.
                    yield prefiltered
                    continue

                if followers is not None and bio is not None:
                    fetch = self._profile_from_videos(
                        niche=niche,
                        handle=handle,
                        followers=followers,
                        bio=str(bio),
                        feed_hashtags=set(),
                        external_links=external_links,
                    )
                else:
                    fetch = self._profile_from_user(handle, niche=niche)
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
                await self._embed_delay()
            if on_finished is not None:
                on_finished(len(seen_handles))
        except TikTokBlockedError:
            raise
        except Exception as error:
            blocked = await self._handle_feed_error(error, context=f"search '{query}'")
            if blocked:
                raise TikTokBlockedError(f"feed requests blocked on search '{query}'") from error
        finally:
            if self.restart_session_between_hashtags and not blocked:
                await self._restart_session()

    async def profiles_from_following(
        self,
        seed: str,
        *,
        niche: str,
        sec_uid: str | None,
        cursor: int,
        max_pages: int,
        on_progress: Callable[[str | None, int, FollowingState], None],
        exclude_handles: set[str] | None = None,
        on_known: Callable[[str], None] | None = None,
        on_seed: Callable[[str, str | None], None] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        """Creators `seed` follows, from `cursor` on, at most `max_pages`
        pages. `on_progress` gets (sec_uid, cursor to resume from, state)
        after each fully consumed page; a feed error reports nothing, so the
        seed stays due. `on_seed` gets (handle, sec_uid) of each public
        creator worth crawling in turn, with or without an email."""
        api = self._api()
        context = f"@{seed}'s following list"
        excluded = self._normalize_excluded(exclude_handles)
        seen_handles: set[str] = set()
        consecutive_blocked = 0
        on_niche_seen = 0
        blocked = False
        try:
            if sec_uid is None:
                self.embed_request_count += 2  # embed page + video page
                sec_uid = await asyncio.to_thread(fetch_sec_uid, seed)
                if sec_uid is None:
                    logger.info("no following list for @%s: creator gone or has no videos", seed)
                    on_progress(None, cursor, "failed")
                    return
                on_progress(sec_uid, cursor, "due")
            for page in range(max_pages):
                if page:
                    await self._delay()
                self.request_count += 1
                resp = await api.make_request(
                    url="https://www.tiktok.com/api/user/list/",
                    params={"secUid": sec_uid, "scene": 21, "count": 30, "minCursor": cursor, "maxCursor": 0},
                )
                if resp is None:
                    raise InvalidResponseException(resp, "TikTok returned an invalid response.")
                status = self._parse_int(resp.get("statusCode"))
                if status == _FOLLOWING_LIST_HIDDEN:
                    logger.info("@%s hides who they follow", seed)
                    on_progress(sec_uid, cursor, "hidden")
                    return
                if status != 0:
                    logger.warning("%s answered statusCode %s", context, status)
                    on_progress(sec_uid, cursor, "failed")
                    return
                self._feed_block_streak = 0
                users = resp.get("userList", []) or []
                for entry in users:
                    user = self._get(entry, "user", default={})
                    stats = self._get(entry, "stats", default={})
                    handle = str(self._get(user, "uniqueId", default="")).strip()
                    normalized_handle = handle.removeprefix("@").lower()
                    if not handle or normalized_handle in seen_handles:
                        continue
                    followers = self._parse_int(self._get(stats, "followerCount", default=None))
                    bio = self._get(user, "signature", default=None)
                    bio_text = str(bio or "")
                    external_links = self._extract_bio_links(user)
                    is_gym_account = (
                        (followers or 0) >= self.min_followers
                        and bio_on_niche(niche, bio_text)
                        and rejected_group(niche, bio_text, [], "") is None
                    )
                    if is_gym_account:
                        on_niche_seen += 1
                    if (
                        on_seed is not None
                        and (followers or 0) >= self.min_followers
                        and (self.max_followers is None or (followers or 0) <= self.max_followers)
                        and not self._get(user, "privateAccount", default=False)
                        and seed_bio_on_niche(niche, bio_text)
                        and rejected_group(niche, bio_text, [], "") is None
                    ):
                        on_seed(normalized_handle, self._get(user, "secUid", default=None) or None)
                    if normalized_handle in excluded:
                        logger.info("skipping @%s from %s: handle already evaluated", handle, context)
                        if on_known is not None:
                            on_known(normalized_handle)
                        continue
                    seen_handles.add(normalized_handle)

                    prefiltered = self._prefiltered_candidate(
                        niche=niche,
                        handle=handle,
                        followers=followers,
                        bio=bio,
                        external_links=external_links,
                        check_rejected_group=False,
                    )
                    if prefiltered is not None:
                        # No pause: the skip made no request, and list pages stay a full delay apart.
                        yield prefiltered
                        continue
                    # Left unrecorded: it was not evaluated, and a hashtag crawl may still reach it.
                    if not bio_on_niche(niche, bio_text):
                        logger.info("skipping @%s from %s: bio is not about %s", handle, context, niche)
                        continue
                    bio_emails = extract_emails(bio_text, *external_links)
                    if rejected_group(niche, bio_text, [], bio_emails[0] if bio_emails else "") is not None:
                        yield CandidateProfile(
                            handle=handle,
                            profile_url=f"https://www.tiktok.com/@{handle}",
                            niche=niche,
                            followers_count=followers,
                            bio=bio_text,
                            external_links=external_links,
                            source="tiktokapi",
                        )
                        continue

                    if followers is not None and bio is not None:
                        fetch = self._profile_from_videos(
                            niche=niche,
                            handle=handle,
                            followers=followers,
                            bio=str(bio),
                            feed_hashtags=set(),
                            external_links=external_links,
                            sec_uid=self._get(user, "secUid", default=None) or None,
                        )
                    else:
                        fetch = self._profile_from_user(handle, niche=niche)
                    result = await self._safe_fetch(fetch, handle=handle, context=f"from {context}")
                    if result.profile is not None:
                        consecutive_blocked = 0
                        yield result.profile
                    elif result.blocked:
                        consecutive_blocked += 1
                        if await self._recover_if_blocked(consecutive_blocked, context=context):
                            blocked = True
                            raise TikTokBlockedError(f"sustained TikTok blocking on {context}")
                    await self._embed_delay()
                cursor = self._parse_int(resp.get("minCursor")) or 0
                has_more = bool(resp.get("hasMore", False)) and bool(users) and cursor != 0
                if (
                    has_more
                    and self.following_early_stop_pages > 0
                    and (page + 1) >= self.following_early_stop_pages
                    and on_niche_seen < self.following_min_on_niche_per_stop
                ):
                    logger.info(
                        "stopping %s early after %s page(s): only %s on-niche account(s)",
                        context,
                        page + 1,
                        on_niche_seen,
                    )
                    on_progress(sec_uid, cursor, "done")
                    return
                on_progress(sec_uid, cursor, "due" if has_more else "done")
                if not has_more:
                    return
        except TikTokBlockedError:
            raise
        except Exception as error:
            blocked = await self._handle_feed_error(error, context=context)
            if blocked:
                raise TikTokBlockedError(f"feed requests blocked on {context}") from error
        finally:
            if self.restart_session_between_hashtags and not blocked:
                await self._restart_session()

    async def profiles_from_related(
        self,
        seed: str,
        *,
        niche: str,
        max_videos: int = 3,
        exclude_handles: set[str] | None = None,
        on_seed: Callable[[str, str | None], None] | None = None,
    ) -> AsyncIterable[CandidateProfile]:
        """Creators surfaced by TikTok's related-video feed for up to
        `max_videos` recent videos of `seed`. Works even when `seed` hides
        their following list."""
        api = self._api()
        context = f"@{seed}'s related videos"
        excluded = self._normalize_excluded(exclude_handles)
        seen_handles: set[str] = {seed.removeprefix("@").lower()}
        consecutive_blocked = 0
        blocked = False
        try:
            self.embed_request_count += 1
            embed = await asyncio.to_thread(fetch_embed, seed)
            video_ids = list(embed.video_ids[: max(1, max_videos)]) if embed else []
            if not video_ids:
                logger.info("no recent videos for related expansion of @%s", seed)
                return
            for idx, video_id in enumerate(video_ids):
                if idx:
                    await self._delay()
                self.request_count += 1
                resp = await api.make_request(
                    url="https://www.tiktok.com/api/related/item_list/",
                    params={"itemID": video_id, "count": 16},
                )
                if resp is None:
                    raise InvalidResponseException(resp, "TikTok returned an invalid response.")
                status = self._parse_int(resp.get("statusCode"))
                if status not in (None, 0):
                    logger.warning("%s (video %s) answered statusCode %s", context, video_id, status)
                    continue
                self._feed_block_streak = 0
                items = resp.get("itemList") or []
                for item in items:
                    author = self._get(item, "author", default={})
                    stats = self._get(item, "authorStats", "author_stats", default={})
                    handle = str(self._get(author, "uniqueId", "unique_id", default="")).strip()
                    normalized_handle = handle.removeprefix("@").lower()
                    if not handle or normalized_handle in seen_handles:
                        continue
                    followers = self._parse_int(self._get(stats, "followerCount", "follower_count", default=None))
                    bio = self._get(author, "signature", default=None) if isinstance(author, dict) else None
                    bio_text = str(bio or "")
                    if (
                        on_seed is not None
                        and (followers or 0) >= self.min_followers
                        and (self.max_followers is None or (followers or 0) <= self.max_followers)
                        and not self._get(author, "privateAccount", default=False)
                        and seed_bio_on_niche(niche, bio_text)
                        and rejected_group(niche, bio_text, [], "") is None
                    ):
                        on_seed(normalized_handle, self._get(author, "secUid", default=None) or None)
                    if normalized_handle in excluded:
                        logger.info("skipping @%s from %s: handle already evaluated", handle, context)
                        continue
                    seen_handles.add(normalized_handle)

                    prefiltered = self._candidate_from_feed_item(item, niche=niche, handle=handle)
                    if prefiltered is not None:
                        yield prefiltered
                        continue

                    result = await self._fetch_candidate(item, niche=niche, handle=handle, context=f"from {context}")
                    if result.profile is not None:
                        consecutive_blocked = 0
                        yield result.profile
                    elif result.blocked:
                        consecutive_blocked += 1
                        if await self._recover_if_blocked(consecutive_blocked, context=context):
                            blocked = True
                            raise TikTokBlockedError(f"sustained TikTok blocking on {context}")
                    await self._embed_delay()
        except TikTokBlockedError:
            raise
        except Exception as error:
            blocked = await self._handle_feed_error(error, context=context)
            if blocked:
                raise TikTokBlockedError(f"feed requests blocked on {context}") from error
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
        inspected: _Counter | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Same pagination as Hashtag.videos, but starts at an arbitrary cursor
        and reports the next page's cursor after each fully-consumed page (0
        once the feed is exhausted, so the next crawl wraps back to the top).

        When `inspected` is given, the caller increments it for each item it
        actually evaluates and pagination stops once that reaches `limit`.
        Raw feed items are still capped (MAX_FEED_ITEMS_PER_NEW_CANDIDATE x
        limit) so a fully-seen feed can't page forever."""
        challenge = api.hashtag(name=tag)
        if getattr(challenge, "id", None) is None:
            self.request_count += 1
            await challenge.info()
        found = 0
        page = 0
        raw_cap = limit * MAX_FEED_ITEMS_PER_NEW_CANDIDATE
        while (inspected.value if inspected is not None else found) < limit and found < raw_cap:
            if page:
                await self._feed_page_delay()
            page += 1
            self.request_count += 1
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
            self.request_count += 1
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
            external_links=self._extract_bio_links(author),
        )

    def _prefiltered_candidate(
        self,
        *,
        niche: str,
        handle: str,
        followers: int | None,
        bio: Any,
        discovered_hashtags: list[str] | None = None,
        external_links: list[str] | None = None,
        check_rejected_group: bool = True,
    ) -> CandidateProfile | None:
        """Disqualify a creator using only feed/search/following payload data
        (zero requests). Returns a candidate the filters will reject, or None
        when the creator either passes the prefilters or the payload lacks the
        data to decide."""
        if followers is None:
            return None
        links = list(external_links or [])
        bio_text = str(bio or "")
        candidate = CandidateProfile(
            handle=handle,
            profile_url=f"https://www.tiktok.com/@{handle}",
            niche=niche,
            followers_count=followers,
            bio=bio_text,
            external_links=links,
            source="tiktokapi",
            discovered_hashtags=discovered_hashtags or [],
        )
        if self.skip_videos_below_min_followers and followers < self.min_followers:
            return candidate
        if self.max_followers is not None and followers > self.max_followers:
            return candidate
        bio_emails = extract_emails(bio_text, *links)
        if self.skip_videos_without_email and bio is not None and not bio_emails:
            # A bio that references a link page (Linktree etc.), has a
            # structured TikTok bioLink, or is a high-signal gym bio with a
            # link-in-bio CTA may hide its email one hop away.
            if self.resolve_bio_link_emails and (
                links
                or bio_hints_link_page(bio_text)
                or self._worth_no_email_bio_link_probe(niche, bio_text)
            ):
                return None
            return candidate
        # A bio word or country-domain email in rejected_group always disqualifies
        # regardless of recent captions, so skip the embed fetch immediately.
        if (
            check_rejected_group
            and bio is not None
            and rejected_group(niche, bio_text, [], bio_emails[0] if bio_emails else "") is not None
        ):
            return candidate
        return None

    async def _fetch_candidate(
        self,
        item: dict[str, Any],
        *,
        niche: str,
        handle: str,
        context: str,
    ) -> _Fetch:
        """Fetch what the feed couldn't provide: the recent videos when it
        gave followers and bio, the whole profile otherwise. One embed-page
        request either way."""
        author = self._get(item, "author", default={})
        stats = self._get(item, "authorStats", "author_stats", default={})
        followers = self._parse_int(self._get(stats, "followerCount", "follower_count", default=None))
        bio = self._get(author, "signature", default=None) if isinstance(author, dict) else None
        external_links = self._extract_bio_links(author)
        if followers is not None and bio is not None:
            return await self._safe_fetch(
                self._profile_from_videos(
                    niche=niche,
                    handle=handle,
                    followers=followers,
                    bio=str(bio),
                    feed_hashtags=self._extract_hashtags(item),
                    external_links=external_links,
                    sec_uid=self._get(author, "secUid", default=None) or None,
                ),
                handle=handle,
                context=context,
            )
        return await self._safe_fetch(
            self._profile_from_user(handle, niche=niche),
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
        except (NotFoundException, KeyError) as error:
            logger.warning("skipped @%s%s: profile unavailable (%s)", handle, suffix, error)
            return _Fetch(None)
        except Exception as error:
            if self._is_dead_session_error(error):
                logger.warning("all sessions died while fetching @%s%s; restarting", handle, suffix)
                await self._restart_session()
                return _Fetch(None)
            # TikTok refusing every profile shows up here (2026-11-09: a non-JSON
            # body), so an unexplained failure counts toward the block streak.
            logger.exception("failed to fetch profile @%s%s", handle, suffix)
            return _Fetch(None, blocked=True)

    def _worth_no_email_bio_link_probe(self, niche: str, bio: str) -> bool:
        return (
            self.resolve_bio_link_emails
            and (self.probe_no_email_bio_links or self.fetch_bio_link_via_detail)
            and seed_bio_on_niche(niche, bio)
            and rejected_group(niche, bio, [], "") is None
        )

    async def _profile_from_videos(
        self,
        *,
        niche: str,
        handle: str,
        followers: int,
        bio: str,
        feed_hashtags: set[str],
        external_links: list[str] | None = None,
        sec_uid: str | None = None,
    ) -> CandidateProfile:
        links = list(external_links or [])
        extra_emails: list[str] = []
        has_bio_email = bool(extract_emails(bio, *links))
        defer_link_probe = False
        if not has_bio_email:
            # First resolve any explicit URL already present in the bio / bioLink.
            if links or bio_hints_link_page(bio):
                extra_emails = await self._resolve_bio_link_emails(handle, bio, links)
            if not extra_emails:
                if self._worth_no_email_bio_link_probe(niche, bio):
                    # Pre-qualify on free embed views + captions BEFORE probing
                    # Linktree/Stan or calling /api/user/detail/.
                    defer_link_probe = True
                elif self.skip_videos_without_email:
                    logger.info("skipping recent videos for @%s: no email behind bio link", handle)
                    return CandidateProfile(
                        handle=handle,
                        profile_url=f"https://www.tiktok.com/@{handle}",
                        niche=niche,
                        followers_count=followers,
                        bio=bio,
                        external_links=links,
                        source="tiktokapi",
                        discovered_hashtags=sorted(feed_hashtags),
                    )
            if extra_emails and rejected_group(niche, bio, [], extra_emails[0]) is not None:
                return CandidateProfile(
                    handle=handle,
                    profile_url=f"https://www.tiktok.com/@{handle}",
                    niche=niche,
                    followers_count=followers,
                    bio=bio,
                    external_links=links,
                    source="tiktokapi",
                    discovered_hashtags=sorted(feed_hashtags),
                    extra_emails=extra_emails,
                )
        views, captions, hashtags = await self._collect_recent_videos(handle)
        hashtags |= feed_hashtags

        if defer_link_probe:
            extra_emails, extra_links = await self._resolve_missing_email(
                niche=niche,
                handle=handle,
                bio=bio,
                views=views,
                captions=captions,
                sec_uid=sec_uid,
            )
            if extra_links:
                links = [*links, *extra_links]

        return CandidateProfile(
            handle=handle,
            profile_url=f"https://www.tiktok.com/@{handle}",
            niche=niche,
            followers_count=followers,
            recent_video_views=views,
            recent_video_captions=captions,
            bio=bio,
            external_links=links,
            source="tiktokapi",
            discovered_hashtags=sorted(hashtags),
            extra_emails=extra_emails,
        )

    async def _resolve_missing_email(
        self,
        *,
        niche: str,
        handle: str,
        bio: str,
        views: list[int],
        captions: list[str],
        sec_uid: str | None = None,
    ) -> tuple[list[str], list[str]]:
        """For a creator with no bio email who already passed followers, verify
        views + niche + English first, then try:
          1) Zero-TikTok direct handle probe (linktr.ee / stan.store / bio.site)
          2) Selective Playwright /api/user/detail/ to read TikTok's clickable bioLink.
        """
        if not self.resolve_bio_link_emails:
            return [], []
        avg_views = robust_average_view_count(views)
        rounded_views = floor_view_count(avg_views) if avg_views is not None else None
        if (
            rounded_views is None
            or rounded_views < self.min_average_views
            or not is_on_niche(niche, bio, captions)
            or rejected_group(niche, bio, captions, "") is not None
        ):
            return [], []
        extra_emails: list[str] = []
        extra_links: list[str] = []
        if self.probe_no_email_bio_links:
            extra_emails = await asyncio.to_thread(
                probe_handle_bio_links,
                handle,
                niche=niche,
                timeout_seconds=min(6.0, self.bio_link_timeout_seconds),
            )
        if not extra_emails and self.fetch_bio_link_via_detail and self.api is not None:
            detail_link = await self._fetch_bio_link_via_detail(handle, sec_uid=sec_uid)
            if detail_link:
                extra_links.append(detail_link)
                extra_emails = await self._resolve_bio_link_emails(handle, "", [detail_link])
        return extra_emails, extra_links

    async def _fetch_bio_link_via_detail(self, handle: str, *, sec_uid: str | None = None) -> str | None:
        """Read TikTok's clickable bioLink.link via /api/user/detail/ on the warm
        Playwright session. Only called after a creator already passed followers,
        views, gym niche, and English gates. Never raises on block/error."""
        if self.api is None:
            return None
        try:
            self.request_count += 1
            resp = await self.api.make_request(
                url="https://www.tiktok.com/api/user/detail/",
                params={"uniqueId": handle, "secUid": sec_uid or ""},
            )
        except Exception as error:
            logger.warning("bioLink detail lookup failed for @%s: %s", handle, error)
            return None
        user = self._get(self._get(resp or {}, "userInfo", default={}), "user", default={})
        links = self._extract_bio_links(user)
        if links:
            logger.info("found TikTok bioLink for @%s: %s", handle, links[0])
            return links[0]
        return None

    async def _profile_from_user(self, handle: str, *, niche: str) -> CandidateProfile:
        """The whole profile from the embed page, one unsigned request. The
        signed user-detail call it replaces failed for every profile on
        2026-11-09."""
        self.embed_request_count += 1
        embed = await asyncio.to_thread(fetch_embed, handle)
        if embed is None:
            # Deleted or renamed. Returned without followers so the runner
            # records the handle; left unrecorded, a recheck retries it every sweep.
            logger.info("@%s no longer exists", handle)
            return CandidateProfile(
                handle=handle,
                profile_url=f"https://www.tiktok.com/@{handle}",
                niche=niche,
                source="tiktokapi",
            )
        extra_emails: list[str] = []
        external_links: list[str] = []
        in_follower_range = embed.followers >= self.min_followers and (
            self.max_followers is None or embed.followers <= self.max_followers
        )
        if in_follower_range and not extract_emails(embed.bio):
            extra_emails = await self._resolve_bio_link_emails(embed.handle, embed.bio, [])
            if not extra_emails:
                extra_emails, external_links = await self._resolve_missing_email(
                    niche=niche,
                    handle=embed.handle,
                    bio=embed.bio,
                    views=embed.views,
                    captions=embed.captions,
                )
        return CandidateProfile(
            handle=embed.handle,
            profile_url=f"https://www.tiktok.com/@{embed.handle}",
            niche=niche,
            followers_count=embed.followers,
            recent_video_views=embed.views,
            recent_video_captions=embed.captions,
            bio=embed.bio,
            external_links=external_links,
            source="tiktokapi",
            discovered_hashtags=sorted(
                {tag.lower() for caption in embed.captions for tag in _CAPTION_HASHTAG.findall(caption)}
            ),
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

    async def _collect_recent_videos(self, handle: str) -> tuple[list[int], list[str], set[str]]:
        """Recent videos from the embed page, one unsigned request. The signed
        user-videos call it replaces was refused for every creator from
        2026-11-12 01:20."""
        self.embed_request_count += 1
        embed = await asyncio.to_thread(fetch_embed, handle)
        if embed is None:
            return [], [], set()
        hashtags = {tag.lower() for caption in embed.captions for tag in _CAPTION_HASHTAG.findall(caption)}
        return embed.views, embed.captions, hashtags

    async def _delay(self) -> None:
        delay = self.request_delay_seconds
        if self.request_jitter_seconds > 0:
            delay += random.uniform(0, self.request_jitter_seconds)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _embed_delay(self) -> None:
        if self.request_delay_seconds <= 0 and self.request_jitter_seconds <= 0:
            return
        delay = self.embed_delay_seconds
        if self.embed_jitter_seconds > 0:
            delay += random.uniform(0, self.embed_jitter_seconds)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _feed_page_delay(self) -> None:
        if self.request_delay_seconds <= 0 and self.request_jitter_seconds <= 0:
            return
        await asyncio.sleep(random.uniform(0.5, 1.2))

    async def _feed_skip_delay(self) -> None:
        # Kept as a no-op compatibility hook for tests; paging is paced once per
        # page in _feed_page_delay instead of sleeping after zero-request skips.
        return None

    @staticmethod
    def _normalize_excluded(exclude_handles: set[str] | None) -> set[str]:
        if not exclude_handles:
            return set()
        # Repository sets (100k-700k handles) are already normalized lowercase
        # without '@'; avoid copying them on every work item.
        if len(exclude_handles) >= 10_000:
            return exclude_handles
        return {handle.removeprefix("@").lower() for handle in exclude_handles}

    @classmethod
    def _extract_bio_links(cls, user_data: Any) -> list[str]:
        if not isinstance(user_data, dict):
            return []
        raw = cls._get(user_data, "bioLink", "bio_link", default=None)
        if isinstance(raw, dict):
            link = str(raw.get("link") or "").strip()
            return [link] if link else []
        if isinstance(raw, str) and raw.strip():
            return [raw.strip()]
        return []

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
