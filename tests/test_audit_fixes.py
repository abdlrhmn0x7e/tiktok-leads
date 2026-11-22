import asyncio

from tiktok_leads.bio_links import _is_noise
from tiktok_leads.db import LeadRepository
from tiktok_leads.factory import build_source
from tiktok_leads.filters import evaluate_candidate
from tiktok_leads.models import CandidateProfile
from tiktok_leads.niches import (
    is_harvestable_hashtag,
    rejected_group,
    seed_bio_on_niche,
    yahoo_queries_for_niche,
)
from tiktok_leads.runner import scrape_following, scrape_hashtag
from tiktok_leads.settings import Settings
from tiktok_leads.sources import tiktokapi_source
from tiktok_leads.sources.embed import EmbedProfile


def _eval(bio: str, captions: list[str], *, email: str = "coach@gmail.com", followers: int = 50_000, max_followers: int | None = None):
    return evaluate_candidate(
        CandidateProfile(
            handle="creator",
            profile_url="https://www.tiktok.com/@creator",
            niche="fitness",
            followers_count=followers,
            recent_video_views=[30_000] * max(1, len(captions)),
            recent_video_captions=captions,
            bio=f"{bio} {email}",
        ),
        min_followers=10_000,
        min_average_views=10_000,
        max_followers=max_followers,
    )


def test_tightened_fitness_gate_blocks_false_positives_and_keeps_gym_creators():
    # False positives observed in labeled.jsonl and Nov 21 live leads
    assert _eval("Korean Lash Lift & Extensions Specialist", ["The lash lift effect", "lashes to lift"]).skip_code == "off_niche"
    assert _eval("get my gymnastics routine below", ["Gymnasts should be in horror movies"]).skip_code == "off_niche"
    assert _eval("millennial dad of 3 • garage gym", ["garage gym session"]).skip_code == "off_niche"
    assert _eval("Horse trainer & stable life", ["training horses", "horse trainer tips", "barn day"]).skip_code == "off_niche"
    assert _eval("Certified dog trainer | K9", ["dog training tips", "canine work", "puppy work"]).skip_code == "off_niche"
    assert _eval("Relationship & dating coach | Avoidant attachment", ["coaching clients", "dating tips"]).skip_code == "off_niche"
    assert _eval("DM for songs promo | Spotify", ["gym edit", "song promo"]).skip_code == "off_niche"
    assert _eval("Softball athlete", ["softball workout", "batting cage"]).skip_code == "off_niche"
    assert _eval("Calorie tracker", ["8 examples of 150 calories", "8 examples of 800 calories", "200 calories"]).skip_code == "off_niche"
    assert _eval("Fatherhood | Fitness | Fashion", ["gym workout"]).skip_code == "off_niche"
    assert _eval("Promotions Telegram/Kik: Unionmedia", ["gym shred", "workout"]).skip_code == "off_niche"
    assert _eval("Longevity | Nutrition | Biohacking", ["strength training"]).skip_code == "off_niche"
    assert _eval("Pop culture. Weight loss. Everyday life.", ["weight loss journey"]).skip_code == "off_niche"
    assert _eval("Natural Hair | Beauty | Fitness", ["gym session"]).skip_code == "off_niche"
    assert _eval("Skits | Fitness | Lifestyle", ["gym skit"]).skip_code == "off_niche"

    # Real gym creators (including gymwear brand athletes and CPTs) pass
    assert _eval("@DFYNE athlete | code DEJFIT", ["heavy leg day squats"]).lead is not None
    assert _eval("CPT | Online fitness coach", ["back day barbell rows"]).lead is not None
    assert _eval("Body Building | 1:1 prep coach", ["chest day bench press"]).lead is not None


def test_rejected_group_and_harvest_rules_cover_new_exclusions():
    assert rejected_group("fitness", "Online fitness coach", [], "coach@fitness.co.nz") == "email on a country domain"
    assert rejected_group("fitness", "Crossfit athlete", [], "a@gmail.com") == "sport"
    assert not is_harvestable_hashtag("horsetrainersoftiktok", "fitness")
    assert not is_harvestable_hashtag("dogtrainer", "fitness")
    assert not is_harvestable_hashtag("gymnastics", "fitness")
    assert not is_harvestable_hashtag("proteinmaxxing", "fitness")
    assert is_harvestable_hashtag("personaltrainer", "fitness")
    assert is_harvestable_hashtag("chestday", "fitness")
    assert seed_bio_on_niche("fitness", "Online fitness coach | hypertrophy")
    assert not seed_bio_on_niche("fitness", "Relationship coaching | mindset")
    assert not seed_bio_on_niche("fitness", "Nutrition & healthy habits")
    # Yahoo queries should no longer contain rejected food/running/mom/crossfit terms
    queries = " ".join(yahoo_queries_for_niche("fitness")).lower()
    for banned in ("hyrox", "crossfit", "meal prep", "marathon", "fit mom", "dietitian", "yoga instructor"):
        assert banned not in queries


def test_max_followers_cap_and_bio_only_veto_before_views():
    assert _eval("Powerlifter", ["squat PR"], followers=300_000, max_followers=250_000).skip_code == "high_followers"
    # Even with no videos yet (prefiltered), a rejected-group bio returns off_niche rather than low_views.
    ev = evaluate_candidate(
        CandidateProfile(
            handle="runner",
            profile_url="https://www.tiktok.com/@runner",
            niche="fitness",
            followers_count=50_000,
            bio="Marathon runner | gym runner@gmail.com",
        ),
        min_followers=10_000,
        min_average_views=10_000,
    )
    assert ev.skip_code == "off_niche"


def test_bio_link_noise_filter_blocks_platform_and_generic_addresses():
    assert _is_noise("errors@sentry.io")
    assert _is_noise("noreply@coachsite.com")
    assert _is_noise("privacy@customgym.com")
    assert _is_noise("support@linktr.ee")
    assert not _is_noise("coach@customgym.com")


def test_db_wal_index_and_niche_scoped_hashtag_harvesting(tmp_path):
    repo = LeadRepository(tmp_path / "leads.sqlite")
    repo.initialize()
    mode = repo.connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    indexes = {
        row["name"]
        for row in repo.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'scraped_profiles'"
        )
    }
    assert "idx_scraped_profiles_last_seen" in indexes

    # Off-niche hashtags must not be persisted into discovered_hashtags for fitness.
    repo.record_discovered_hashtags(["gymtok", "makeup", "horsetrainersoftiktok", "chestday"], niche="fitness")
    saved = {
        row["hashtag"]
        for row in repo.connection.execute("SELECT hashtag FROM discovered_hashtags WHERE niche = 'fitness'")
    }
    assert saved == {"gymtok", "chestday"}


def test_level2_following_seed_does_not_promote_more_seeds_and_early_stops(monkeypatch, tmp_path):
    repo = LeadRepository(tmp_path / "leads.sqlite")
    repo.initialize()
    repo.add_found_seed("level2.seed", "fitness", "MS4w-lvl2")

    class FakeApi:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = 0

        async def make_request(self, *, url, params):
            self.calls += 1
            return self.responses.pop(0)

    class NoopNotifier:
        def send(self, lead):
            pass

        def send_found_via(self, lead):
            pass

    # 3 pages available, but pages 1-2 have 0 on-niche accounts -> early-stops after page 2.
    def noise_page(cursor):
        return {
            "statusCode": 0,
            "userList": [
                {
                    "user": {"uniqueId": f"dog{cursor}", "signature": "Dog lover", "secUid": f"s-{cursor}", "privateAccount": False},
                    "stats": {"followerCount": 50_000},
                }
            ],
            "hasMore": True,
            "minCursor": cursor,
        }

    api = FakeApi([noise_page(101), noise_page(102), noise_page(103)])
    source = build_source(
        Settings(
            _env_file=None,
            scrape_profile_delay_seconds=0,
            scrape_profile_jitter_seconds=0,
            resolve_bio_link_emails=False,
            discovery_following_early_stop_pages=2,
            discovery_following_min_on_niche_per_stop=2,
        )
    )
    source.api = api
    asyncio.run(
        scrape_following(
            source,
            repo,
            NoopNotifier(),
            seed="level2.seed",
            max_pages=6,
            min_followers=10_000,
            min_average_views=10_000,
        )
    )
    assert api.calls == 2
    assert repo.following_seed("level2.seed").state == "done"

    # Now test that even when a level-2 seed follows an on-niche gym creator, it does NOT add them as a new seed.
    repo.add_found_seed("level2.gym", "fitness", "MS4w-lvl2gym")
    api2 = FakeApi(
        [
            {
                "statusCode": 0,
                "userList": [
                    {
                        "user": {"uniqueId": "nested.lifter", "signature": "Powerlifter", "secUid": "s-nested", "privateAccount": False},
                        "stats": {"followerCount": 50_000},
                    }
                ],
                "hasMore": False,
                "minCursor": 0,
            }
        ]
    )
    source.api = api2
    asyncio.run(
        scrape_following(
            source,
            repo,
            NoopNotifier(),
            seed="level2.gym",
            max_pages=6,
            min_followers=10_000,
            min_average_views=10_000,
        )
    )
    assert repo.following_seed("nested.lifter") is None


def test_hashtag_prefilter_skips_embed_for_rejected_group_and_updates_exclude_handles(monkeypatch, tmp_path):
    repo = LeadRepository(tmp_path / "leads.sqlite")
    repo.initialize()
    embed_calls = []

    def fake_embed(handle):
        embed_calls.append(handle)
        return EmbedProfile(
            handle=handle,
            followers=50_000,
            bio="Online fitness coach",
            views=[30_000, 30_000, 30_000],
            captions=["leg day squat", "back day", "bench press"],
            video_ids=["1"],
        )

    monkeypatch.setattr(tiktokapi_source, "fetch_embed", fake_embed)

    class FakeHashtag:
        id = "123"

    class FakeApi:
        def hashtag(self, name):
            return FakeHashtag()

        async def make_request(self, *, url, params):
            return {
                "itemList": [
                    {
                        "author": {
                            "uniqueId": "marathon.dan",
                            "signature": "Marathon runner dan@gmail.com",
                        },
                        "authorStats": {"followerCount": 50_000},
                        "textExtra": [{"hashtagName": "chestday"}, {"hashtagName": "marathon"}],
                    },
                    {
                        "author": {
                            "uniqueId": "gym.sarah",
                            "signature": "Online fitness coach sarah@gmail.com",
                            "bioLink": {"link": "https://linktr.ee/gymsarah"},
                        },
                        "authorStats": {"followerCount": 50_000},
                        "textExtra": [{"hashtagName": "deadlift"}],
                    },
                ],
                "hasMore": False,
                "cursor": 0,
            }

    class Notifier:
        def __init__(self):
            self.sent = []

        def send(self, lead):
            self.sent.append(lead.handle)

        def send_found_via(self, lead):
            pass

    source = build_source(
        Settings(_env_file=None, scrape_profile_delay_seconds=0, scrape_profile_jitter_seconds=0, resolve_bio_link_emails=False)
    )
    source.api = FakeApi()
    exclude = set()
    notifier = Notifier()
    inserted = asyncio.run(
        scrape_hashtag(
            source,
            repo,
            notifier,
            hashtag="gymtok",
            niche="fitness",
            limit=10,
            min_followers=10_000,
            min_average_views=10_000,
            exclude_handles=exclude,
        )
    )
    assert inserted == 1
    # marathon.dan was prefiltered by rejected_group without spending an embed request
    assert embed_calls == ["gym.sarah"]
    assert notifier.sent == ["gym.sarah"]
    assert {"marathon.dan", "gym.sarah"}.issubset(exclude)
    # Off-niche marathon.dan's hashtag was NOT harvested; gym.sarah's #deadlift WAS harvested
    tags = {row["hashtag"] for row in repo.connection.execute("SELECT hashtag FROM discovered_hashtags")}
    assert tags == {"deadlift"}
