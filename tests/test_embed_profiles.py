import asyncio
import json

import pytest

from tiktok_leads.factory import build_source
from tiktok_leads.settings import Settings
from tiktok_leads.sources import tiktokapi_source
from tiktok_leads.sources.embed import EmbedProfile, parse_embed
from tiktok_leads.sources.tiktokapi_source import TikTokBlockedError


def embed_page(creator: dict) -> str:
    state = {"source": {"data": {"/embed/@dan.swole": creator, "strategy": {"wrappers": []}}}}
    return f'<html><script id="__FRONTITY_CONNECT_STATE__" type="application/json">{json.dumps(state)}</script></html>'


def test_embed_page_gives_followers_bio_views_and_captions():
    page = embed_page(
        {
            "isError": False,
            "userInfo": {"uniqueId": "dan.swole", "followerCount": 142300, "signature": "Powerlifter"},
            "videoList": [{"desc": "squat PR #gym", "playCount": 10000}, {"desc": "", "playCount": 837400}],
        }
    )
    assert parse_embed(page) == EmbedProfile(
        handle="dan.swole",
        followers=142300,
        bio="Powerlifter",
        views=[10000, 837400],
        captions=["squat PR #gym", ""],
    )


def test_missing_creator_is_none():
    assert parse_embed(embed_page({"isError": True, "errorCode": 10221, "userInfo": {"uniqueId": "gone"}})) is None


@pytest.mark.parametrize("page", ["<html>captcha</html>", embed_page({"isError": False, "userInfo": {}})])
def test_page_without_creator_data_is_an_error_not_a_missing_creator(page):
    with pytest.raises(ValueError):
        parse_embed(page)


def source(monkeypatch, fetch):
    monkeypatch.setattr(tiktokapi_source, "fetch_embed", fetch)
    return build_source(
        Settings(
            _env_file=None,
            scrape_profile_delay_seconds=0,
            scrape_profile_jitter_seconds=0,
            resolve_bio_link_emails=False,
        )
    )


async def check(source, handle):
    return [profile async for profile in source.profiles_from_handles([handle], niche="fitness")]


def test_five_failed_profile_checks_in_a_row_raise_blocked(monkeypatch):
    def refuse(handle):
        raise RuntimeError("embed page answered HTTP 503")

    blocked = source(monkeypatch, refuse)

    async def run():
        # One handle per call, as the daemon's rechecks arrive.
        for _ in range(4):
            assert await check(blocked, "dan.swole") == []
        with pytest.raises(TikTokBlockedError):
            await check(blocked, "dan.swole")

    asyncio.run(run())


def test_deleted_creator_is_returned_so_it_gets_recorded(monkeypatch):
    deleted = source(monkeypatch, lambda handle: None)

    async def run():
        for _ in range(6):  # never counts as a block
            [profile] = await check(deleted, "gone")
            assert profile.handle == "gone" and profile.followers_count is None

    asyncio.run(run())


def test_creator_from_a_hashtag_feed_gets_views_and_captions_from_the_embed_page(monkeypatch):
    embed = EmbedProfile(handle="dan.swole", followers=1, bio="", views=[40_000, 20_000], captions=["leg day #gym", ""])
    feed_item = {"author": {"uniqueId": "dan.swole", "signature": "Powerlifter dan@example.org"}, "authorStats": {"followerCount": 142_300}}
    fetched = source(monkeypatch, lambda handle: embed)
    result = asyncio.run(fetched._fetch_candidate(feed_item, niche="fitness", handle="dan.swole", context=""))
    assert (result.profile.followers_count, result.profile.recent_video_views) == (142_300, [40_000, 20_000])
    assert result.profile.recent_video_captions == ["leg day #gym", ""]
    assert "gym" in result.profile.discovered_hashtags
