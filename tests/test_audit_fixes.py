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
    assert rejected_group("fitness", "Online fitness coach", [], "maja.ech@gmx.net") == "email on a country domain"
    assert rejected_group("fitness", "Online fitness coach", [], "coach@laposte.net") == "email on a country domain"
    assert rejected_group("fitness", "Online fitness coach", [], "coach@naver.com") == "email on a country domain"
    assert rejected_group("fitness", "Online fitness coach", [], "julia@grail-talent.com") == "blocked email domain"
    assert rejected_group("mom", "Mom of 2", [], "someone@grail-talent.com") == "blocked email domain"
    assert rejected_group("fitness", "Entrena con nosotros aquí", [], "a@gmail.com") == "non-english creator"
    assert rejected_group("fitness", "Personal trainer al femminile | Scopri i miei", [], "a@gmail.com") == "non-english creator"
    assert rejected_group("fitness", "Krafttraining und Ernährung", [], "a@gmail.com") == "non-english creator"
    assert rejected_group("fitness", "Онлайн тренер по фитнесу", [], "a@gmail.com") == "non-english creator"
    # English creator with US city ("Los Angeles") or a heritage flag must still pass
    assert rejected_group("fitness", "Strength • Los Angeles 🇩🇴", ["My go to moves for abs"], "a@gmail.com") is None
    assert rejected_group("fitness", "Crossfit athlete", [], "a@gmail.com") == "sport"
    assert not is_harvestable_hashtag("horsetrainersoftiktok", "fitness")
    assert not is_harvestable_hashtag("dogtrainer", "fitness")
    assert not is_harvestable_hashtag("gymnastics", "fitness")
    assert not is_harvestable_hashtag("proteinmaxxing", "fitness")
    assert not is_harvestable_hashtag("pesasfitness", "fitness")
    assert not is_harvestable_hashtag("kuwaitfitness", "fitness")
    assert not is_harvestable_hashtag("фитнесgym", "fitness")
    assert is_harvestable_hashtag("personaltrainer", "fitness")
    assert is_harvestable_hashtag("chestday", "fitness")
    assert seed_bio_on_niche("fitness", "Online fitness coach | hypertrophy")
    assert not seed_bio_on_niche("fitness", "Relationship coaching | mindset")
    assert not seed_bio_on_niche("fitness", "Nutrition & healthy habits")


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


def test_pinned_viral_outliers_do_not_inflate_average_views_and_troll_pages_rejected():
    from tiktok_leads.niches import is_on_niche
    from tiktok_leads.numbers import robust_average_view_count

    # 3 pinned viral videos + 10 low-view videos (exact @skulltok1 distribution)
    views = [4_400_000, 7_200_000, 8_000_000, 19_100, 4_231, 3_224, 4_072, 435, 1_482, 1_052, 879, 10_300, 1_447]
    avg = robust_average_view_count(views)
    assert avg is not None and avg < 10_000

    # Normal consistent creator with 3 viral pins still passes cleanly
    consistent = [1_500_000, 900_000, 800_000, 25_000, 22_000, 18_000, 15_000, 14_000, 12_000, 11_000, 9_500, 8_000]
    assert robust_average_view_count(consistent) >= 14_000

    # Troll / clip-repost signals rejected even if captions spam #calisthenics / #gymer
    assert rejected_group(
        "fitness",
        "only Coldest Moments 🔥\ncontact For . skulltok1@gmail.com",
        ["#MoneySavingTips #gymer #unfreez", "Hulk version #trollface #gymer #unfreez"],
        "skulltok1@gmail.com",
    ) is not None

    # Holistic / juicing / detox bios rejected even if they mention "meal plans/workouts"
    assert rejected_group(
        "fitness",
        "Holistic nutritionist 🧃\nCertified juice specialist 🫐\nMeal plans/workouts ⬇️",
        ["LOW IRON? Muscle weakness and reduced workout endurance. LET'S MAKE A WELLNESS JUICE!"],
        "info@elitejuicez.com",
    ) is not None

    # Dietitians, supplement brands, affiliate/fashion pages, mental-health/Ramadan lifestyle, Nordic creators
    assert rejected_group("fitness", "Weight loss dietitian\nLose fat & boost metabolism", [], "a@b.com") is not None
    assert rejected_group("fitness", "High-Quality Bodybuilding Supplements\nShop at the link below", [], "info@hugesupplements.com") is not None
    assert rejected_group("fitness", "Affiliate Marketer | Nurse Life | Fitness | Fashion", [], "a@b.com") is not None
    assert rejected_group("fitness", "fitness tips\nislam + mental health tips", [], "a@b.com") is not None
    assert rejected_group("fitness", "Jeg hjelper deg å bli sterkere\nTrening • kosthold • coaching", [], "a@b.com") == "non-english creator"
    assert not is_on_niche("fitness", "lil bit western, weightloss, & chaos", ["Weightloss transformation", "Weightloss motivation", "Weightloss journey"])


def test_enrich_preserves_original_found_via_and_tracks_enrichment(tmp_path):
    from tiktok_leads.models import CandidateProfile
    from tiktok_leads.runner import scrape_handles

    repo = LeadRepository(tmp_path / "leads.sqlite")
    repo.initialize()
    repo.add_found_seed("gymcoach.alex", "fitness", "sec_1")
    repo.record_scraped_profile(
        handle="gymcoach.alex",
        niche="fitness",
        source="tiktokapi",
        skip_reason="no_email",
        found_via="following:@level1seed",
    )
    repo.add_found_seed("gymcoach.sam", "fitness", "sec_2")
    repo.record_scraped_profile(
        handle="gymcoach.sam",
        niche="fitness",
        source="tiktokapi",
        skip_reason="no_email",
        found_via="#bodyrecomposition",
    )

    pairs = repo.no_email_seed_handles("fitness", limit=10)
    assert pairs == [("gymcoach.alex", "following:@level1seed"), ("gymcoach.sam", "#bodyrecomposition")]

    class FakeSource:
        async def profiles_from_handles(self, handles, *, niche):
            yield CandidateProfile(
                handle="gymcoach.alex",
                profile_url="https://www.tiktok.com/@gymcoach.alex",
                niche=niche,
                followers_count=50_000,
                bio="Online gym coach alex@gymcoach.com",
                recent_video_views=[20_000, 22_000, 25_000],
                recent_video_captions=["push day bench press", "leg day squat", "hypertrophy training"],
                source="tiktokapi",
            )
            yield CandidateProfile(
                handle="gymcoach.sam",
                profile_url="https://www.tiktok.com/@gymcoach.sam",
                niche=niche,
                followers_count=40_000,
                bio="Online gym coach no email yet",
                recent_video_views=[20_000, 22_000, 25_000],
                recent_video_captions=["push day bench press", "leg day squat", "hypertrophy training"],
                source="tiktokapi",
            )

    class CaptureNotifier:
        def __init__(self):
            self.leads = []

        def send(self, lead):
            self.leads.append(lead)

        def send_found_via(self, lead):
            pass

    notifier = CaptureNotifier()
    inserted = asyncio.run(
        scrape_handles(
            FakeSource(),
            repo,
            notifier,
            handles=[h for h, _ in pairs],
            niche="fitness",
            min_followers=10_000,
            min_average_views=10_000,
            found_via=None,
            found_via_by_handle=dict(pairs),
            mark_bio_link_enriched=True,
        )
    )
    assert inserted == 1
    assert len(notifier.leads) == 1
    assert notifier.leads[0].found_via == "following:@level1seed"

    # Both handles are now marked enriched and keep their original found_via tags
    rows = {
        r["handle"]: (r["found_via"], r["bio_link_enriched_at"])
        for r in repo.connection.execute("SELECT handle, found_via, bio_link_enriched_at FROM scraped_profiles")
    }
    assert rows["gymcoach.alex"][0] == "following:@level1seed"
    assert rows["gymcoach.alex"][1] is not None
    assert rows["gymcoach.sam"][0] == "#bodyrecomposition"
    assert rows["gymcoach.sam"][1] is not None
    # Neither is returned again by no_email_seed_handles
    assert repo.no_email_seed_handles("fitness", limit=10) == []


def test_build_queue_filters_exhausted_harvested_hashtags_before_cap(tmp_path):
    from tiktok_leads.daemon import _build_queue

    repo = LeadRepository(tmp_path / "leads.sqlite")
    repo.initialize()
    # Two high-mention tags that are currently exhausted, plus one fresh tag behind them
    for _ in range(10):
        repo.record_discovered_hashtags(["upperbodygym", "lowerbodygym"], niche="fitness")
    for _ in range(3):
        repo.record_discovered_hashtags(["tonedglutes"], niche="fitness")
    repo.set_feed_cursor("hashtag-exhausted:upperbodygym", 1)
    repo.set_feed_cursor("hashtag-exhausted:lowerbodygym", 1)

    settings = Settings(
        _env_file=None,
        discovery_use_harvested_hashtags=True,
        discovery_harvested_min_mentions=2,
        discovery_harvested_hashtags_per_niche=2,
        discovery_following_enabled=False,
        discovery_search=False,
        recheck_enabled=False,
    )
    queue = _build_queue(settings, repo, ["fitness"])
    harvested = [item.value for item in queue if item.harvested]
    assert "tonedglutes" in harvested
    assert "upperbodygym" not in harvested
    assert "lowerbodygym" not in harvested


def test_finnish_french_and_noisy_tags_blocked():
    from tiktok_leads.niches import is_harvestable_hashtag

    assert rejected_group(
        "fitness",
        '@DFYNE Athlete | Koodi "VEETI" -10%\nHAE VALMENNUKSEEN👇🏻',
        ["Sä voisit olla ens vuonna TÄYSIN eri versio itestäs #kuntosali", "Tää vois olla sä, jos LOCK INNAISIT"],
        "veetimanagement@gmail.com",
    ) == "non-english creator"
    assert rejected_group(
        "fitness",
        "Street workout & muscu basic fit 💪🏽 Discipline au quotidien, suis l'évolution",
        ["Que penses-tu de mon évolution physique ? #pourtoi", "C’est dur à le trouver ce jeton #muscu"],
        "ettadlaouielies@gmail.com",
    ) == "non-english creator"
    for bad in ("fitnessgrampacertest", "musclecarsonly", "gymleader", "motivacaofitness", "muaythaigym"):
        assert not is_harvestable_hashtag(bad, "fitness"), bad
