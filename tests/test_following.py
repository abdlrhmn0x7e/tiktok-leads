import asyncio

import pytest
from TikTokApi.exceptions import EmptyResponseException

from tiktok_leads.db import LeadRepository
from tiktok_leads.factory import build_source
from tiktok_leads.models import Lead
from tiktok_leads.runner import scrape_following
from tiktok_leads.settings import Settings
from tiktok_leads.sources import tiktokapi_source
from tiktok_leads.sources.embed import EmbedProfile, parse_sec_uid
from tiktok_leads.sources.tiktokapi_source import TikTokBlockedError

SEED = "coach.seed"
SEC_UID = "MS4wLjABAAAA-seed"
FOUND_VIA = f"following:@{SEED}"


def account(handle: str, followers: int, bio: str) -> dict:
    """One userList entry of /api/user/list/, trimmed from a response saved 2026-11-16."""
    return {
        "user": {"uniqueId": handle, "signature": bio, "secUid": f"MS4w-{handle}", "privateAccount": False},
        "stats": {"followerCount": followers, "followingCount": 300},
    }


def page(*accounts: dict, has_more: bool = False, min_cursor: int = 1736955164, status: int = 0) -> dict:
    return {
        "statusCode": status,
        "userList": list(accounts),
        "hasMore": has_more,
        "minCursor": min_cursor,
        "maxCursor": 0,
        "total": len(accounts),
    }


class FakeApi:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.cursors = []

    async def make_request(self, *, url, params):
        assert (url, params["secUid"]) == ("https://www.tiktok.com/api/user/list/", SEC_UID)
        self.cursors.append(params["minCursor"])
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Notifier:
    def __init__(self):
        self.sent = []
        self.sources = []

    def send(self, lead):
        self.sent.append(lead.handle)

    def send_found_via(self, lead):
        self.sources.append((lead.handle, lead.found_via))


def seeded_repository(tmp_path, *, sec_uid=SEC_UID):
    repository = LeadRepository(tmp_path / "leads.sqlite")
    repository.initialize()
    repository.add_following_seeds([(SEED, "fitness")])
    repository.save_following_progress(SEED, sec_uid=sec_uid, cursor=0, state="due")
    return repository


def crawl(monkeypatch, repository, api, *, max_pages=6, source=None):
    """Run the following work item for SEED; returns the notifier and the handles whose videos were fetched."""
    fetched = []

    def fetch_embed(handle):
        fetched.append(handle)
        return EmbedProfile(
            handle=handle,
            followers=1,
            bio="",
            views=[40_000, 40_000, 40_000],
            captions=["leg day", "deadlift PR", "squat form check"],
            video_ids=["7300000000000000001"],
        )

    async def no_pause():
        return None

    monkeypatch.setattr(tiktokapi_source, "fetch_embed", fetch_embed)
    if source is None:
        source = build_source(
            Settings(_env_file=None, scrape_profile_delay_seconds=0, scrape_profile_jitter_seconds=0, resolve_bio_link_emails=False)
        )
    source.api = api
    monkeypatch.setattr(source, "_feed_skip_delay", no_pause)
    notifier = Notifier()
    asyncio.run(
        scrape_following(
            source,
            repository,
            notifier,
            seed=SEED,
            max_pages=max_pages,
            min_followers=10_000,
            min_average_views=10_000,
            exclude_handles=repository.excluded_handles(21),
        )
    )
    return notifier, fetched


def recorded(repository):
    rows = repository.connection.execute(
        "SELECT handle, skip_reason FROM scraped_profiles WHERE found_via = ?", (FOUND_VIA,)
    )
    return {row["handle"]: row["skip_reason"] for row in rows}


def test_each_followed_account_is_skipped_recorded_or_fetched(monkeypatch, tmp_path):
    repository = seeded_repository(tmp_path)
    repository.insert_lead(
        Lead(
            handle="known.lifter",
            profile_url="https://www.tiktok.com/@known.lifter",
            niche="fitness",
            email="known@example.org",
            followers_count=50_000,
            average_views=20_000,
            source="tiktokapi",
        )
    )
    api = FakeApi(
        page(
            account("known.lifter", 50_000, "Powerlifter known@example.org"),
            account("small.lifter", 2_877, "Natty athlete small@example.org"),
            account("rest.day", 52_200, "Gym rat, no DMs"),
            account("chef.amy", 80_000, "Vegan recipes amy@example.org"),
            account("coach.dan", 125_500, "Gym - Coaching dan@example.org"),
        )
    )

    notifier, fetched = crawl(monkeypatch, repository, api)

    assert fetched == ["coach.dan"]
    assert recorded(repository) == {"small.lifter": "low_followers", "rest.day": "no_email", "coach.dan": None}
    assert notifier.sent == ["coach.dan"]
    assert notifier.sources == []
    assert repository.following_seed(SEED).state == "done"


def test_hidden_list_marks_the_seed_hidden(monkeypatch, tmp_path):
    repository = seeded_repository(tmp_path)
    _, fetched = crawl(monkeypatch, repository, FakeApi(page(status=10222)))
    assert fetched == []
    assert repository.following_seed(SEED).state == "hidden"
    assert repository.due_following_seeds("fitness", limit=30) == []


def test_visit_stops_at_its_page_cap_and_the_next_resumes_there(monkeypatch, tmp_path):
    repository = seeded_repository(tmp_path)
    first = FakeApi(page(account("rest.day", 52_200, "Gym rat"), has_more=True, min_cursor=111))
    crawl(monkeypatch, repository, first, max_pages=1)
    assert (repository.following_seed(SEED).state, repository.following_seed(SEED).cursor) == ("due", 111)

    second = FakeApi(page(account("lift.bo", 3_000, "Lifter"), min_cursor=222))
    crawl(monkeypatch, repository, second, max_pages=1)
    assert second.cursors == [111]
    assert repository.following_seed(SEED).state == "done"

    repository.add_following_seeds([(SEED, "fitness")])
    assert repository.following_seed(SEED).state == "done"
    assert repository.due_following_seeds("fitness", limit=30) == []


def test_blocked_list_leaves_the_seed_due_at_its_cursor(monkeypatch, tmp_path):
    repository = seeded_repository(tmp_path)
    repository.save_following_progress(SEED, sec_uid=None, cursor=111, state="due")
    source = build_source(Settings(_env_file=None, scrape_profile_delay_seconds=0, scrape_profile_jitter_seconds=0))
    source._feed_block_streak = 1
    with pytest.raises(TikTokBlockedError):
        crawl(monkeypatch, repository, FakeApi(EmptyResponseException(None, "empty")), source=source)
    seed = repository.following_seed(SEED)
    assert (seed.state, seed.cursor, seed.sec_uid) == ("due", 111, SEC_UID)


@pytest.mark.parametrize(("resolved", "state"), [(SEC_UID, "done"), (None, "failed")])
def test_seed_without_sec_uid_gets_it_resolved_or_fails(monkeypatch, tmp_path, resolved, state):
    repository = seeded_repository(tmp_path, sec_uid=None)
    monkeypatch.setattr(tiktokapi_source, "fetch_sec_uid", lambda handle: resolved)
    crawl(monkeypatch, repository, FakeApi(page()))
    seed = repository.following_seed(SEED)
    assert (seed.state, seed.sec_uid) == (state, resolved)


def test_sec_uid_comes_from_the_video_page():
    snippet = '<script>{"author":{"id":"6611","uniqueId":"coach.dan","secUid":"MS4wLjABAAAAx-9_k","privateAccount":false}}</script>'
    assert parse_sec_uid(snippet) == "MS4wLjABAAAAx-9_k"
    with pytest.raises(ValueError):
        parse_sec_uid("<html>captcha</html>")
