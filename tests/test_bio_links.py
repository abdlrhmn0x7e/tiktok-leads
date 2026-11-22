import asyncio

from tiktok_leads import bio_links
from tiktok_leads.bio_links import (
    _decode_cfemail,
    _is_noise,
    _page_matches_creator,
    bio_hints_link_page,
    bio_suggests_external_link,
    candidate_urls,
    extract_emails_from_html,
    probe_handle_bio_links,
)
from tiktok_leads.factory import build_source
from tiktok_leads.settings import Settings
from tiktok_leads.sources import tiktokapi_source
from tiktok_leads.sources.embed import EmbedProfile


def _encode_cfemail(email: str, key: int = 0x5A) -> str:
    return f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in email)


def test_candidate_urls_recognizes_new_services_strips_emojis_upgrades_https_and_skips_socials():
    bio = (
        "Follow me https://youtube.com/@coach https://www.etsy.com/shop/foo "
        "https://www.fitnessbyfaz.com👇 http://linktr.ee/flfitgirl "
        "msha.ke/gymcoach link.me/lifter pensight.com/x/coach"
    )
    urls = candidate_urls(bio, [])
    assert "https://linktr.ee/flfitgirl" in urls
    assert "https://msha.ke/gymcoach" in urls
    assert "https://link.me/lifter" in urls
    assert "https://pensight.com/x/coach" in urls
    assert "https://www.fitnessbyfaz.com" in urls
    # Link services come before general websites
    assert urls.index("https://linktr.ee/flfitgirl") < urls.index("https://www.fitnessbyfaz.com")
    # Social & storefront links are skipped
    assert not any("youtube.com" in u or "etsy.com" in u for u in urls)


def test_bio_hints_link_page_ignores_social_and_shop_links():
    assert not bio_hints_link_page("Subscribe https://youtube.com/@gymbro and https://amzn.to/abc")
    assert bio_hints_link_page("Coaching -> pensight.com/x/dan")
    assert bio_hints_link_page("Programs: https://danswole.com")


def test_extract_emails_decodes_cfemail_json_unicode_and_ignores_script_js_noise():
    cf_hex = _encode_cfemail("coach.dan@gmail.com")
    page = f"""
    <html>
      <head>
        <style>/* author: fontdev@foundry.com */</style>
        <script>var _0@l.gh = 1; var err = "8c4075d5481d476e945486754f783364@sentry.io";</script>
        <script id="__NEXT_DATA__" type="application/json">
          {{"contact": "hello\\u0040gymcoach.com", "bad": "\\u003eguidelines@patreon.com"}}
        </script>
      </head>
      <body>
        <a href="/cdn-cgi/l/email-protection#{cf_hex}">Email me</a>
        <p>Placeholder: usuario@dominio.com</p>
        <p>Entity: bookings&#64;lifter.io</p>
      </body>
    </html>
    """
    emails = extract_emails_from_html(page)
    assert emails[0] == "coach.dan@gmail.com"  # priority: cfemail / mailto first
    assert "hello@gymcoach.com" in emails
    assert "bookings@lifter.io" in emails
    assert all("patreon.com" not in e for e in emails)
    assert all("sentry.io" not in e for e in emails)
    assert all("dominio.com" not in e for e in emails)
    assert all("_0@l.gh" not in e for e in emails)


def test_noise_filter_and_cfemail_decoder():
    assert _decode_cfemail(_encode_cfemail("test@gym.com")) == "test@gym.com"
    assert _decode_cfemail("zz") is None
    assert _is_noise("_0@l.gh")
    assert _is_noise("u003eguidelines@patreon.com")
    assert _is_noise("usuario@dominio.com")
    assert _is_noise("8c4075d5481d476e945486754f783364@custom.com")
    assert _is_noise("noreply@coach.com")
    assert _is_noise("hello@shopmy.us")
    assert _is_noise("help@skool.com")
    assert _is_noise("help@customgym.com")
    assert not _is_noise("collabs@gymgirl.com")
    assert candidate_urls("https://a.co/d/abc https://docs.google.com/spreadsheets/d/1 https://shopmy.us/foo", []) == []


def test_bio_suggests_external_link_and_handle_probe_ownership(monkeypatch):
    assert bio_suggests_external_link("Online fitness coach | Apply below 👇")
    assert bio_suggests_external_link("1:1 Coaching | Link in bio")
    assert not bio_suggests_external_link("Just lifting heavy weights")

    # Backlink to exact TikTok handle always verifies ownership (even short handles)
    assert _page_matches_creator('<a href="https://www.tiktok.com/@dan">TT</a>', "dan", niche="fitness")
    # Short handle without TikTok backlink is rejected
    assert not _page_matches_creator("<title>Dan | Gym Coach</title><p>Workout plans</p>", "dan", niche="fitness")
    # >=6-char handle matching title + niche copy verifies ownership
    assert _page_matches_creator("<title>dan.swole | Linktree</title><p>Online fitness coach</p>", "dan.swole", niche="fitness")
    # Same >=6-char handle on an unrelated DJ page is rejected
    assert not _page_matches_creator("<title>dan.swole | Linktree</title><p>Techno DJ sets</p>", "dan.swole", niche="fitness")

    pages = {
        "https://linktr.ee/dan.swole": '<title>dan.swole</title><a href="https://tiktok.com/@dan.swole">TT</a><a href="mailto:dan@swole.com">Email</a>',
    }
    monkeypatch.setattr(bio_links, "_fetch_html", lambda url, timeout_seconds=6.0: pages.get(url, ""))
    assert probe_handle_bio_links("dan.swole", niche="fitness") == ["dan@swole.com"]


def test_no_email_gym_creator_prequalifies_then_probes_handle_or_falls_back_to_detail(monkeypatch):
    probe_calls = []
    detail_calls = []
    link_fetches = []

    class FakeApi:
        async def make_request(self, *, url, params):
            detail_calls.append((url, params["uniqueId"]))
            return {"userInfo": {"user": {"bioLink": {"link": "https://linktr.ee/custom_coach_page"}}}}

    def make_source():
        src = build_source(
            Settings(
                _env_file=None,
                scrape_profile_delay_seconds=0,
                scrape_profile_jitter_seconds=0,
                min_followers=10_000,
                min_average_views=10_000,
                resolve_bio_link_emails=True,
                probe_no_email_bio_links=True,
                fetch_bio_link_via_detail=True,
            )
        )
        src.api = FakeApi()
        return src

    # Case 1: Low-view creator stops after embed — zero handle probes, zero /api/user/detail/ calls
    monkeypatch.setattr(
        tiktokapi_source,
        "fetch_embed",
        lambda handle: EmbedProfile(handle=handle, followers=50_000, bio="", views=[1_000, 2_000, 1_500], captions=["leg day"], video_ids=["1"]),
    )
    monkeypatch.setattr(tiktokapi_source, "probe_handle_bio_links", lambda handle, **kw: (probe_calls.append(handle), [])[1])
    src = make_source()
    profile = asyncio.run(
        src._profile_from_videos(niche="fitness", handle="lowviews.fit", followers=50_000, bio="Online fitness coach 👇", feed_hashtags=set())
    )
    assert profile.extra_emails == []
    assert probe_calls == []
    assert detail_calls == []

    # Case 2: Qualified creator found by #2 handle probe -> zero /api/user/detail/ calls
    monkeypatch.setattr(
        tiktokapi_source,
        "fetch_embed",
        lambda handle: EmbedProfile(handle=handle, followers=50_000, bio="", views=[30_000, 40_000, 35_000], captions=["leg day squats"], video_ids=["1"]),
    )
    monkeypatch.setattr(
        tiktokapi_source,
        "probe_handle_bio_links",
        lambda handle, **kw: (probe_calls.append(handle), ["probed@gmail.com"])[1],
    )
    profile2 = asyncio.run(
        src._profile_from_videos(niche="fitness", handle="probed.fit", followers=50_000, bio="Online fitness coach 👇", feed_hashtags=set())
    )
    assert profile2.extra_emails == ["probed@gmail.com"]
    assert probe_calls == ["probed.fit"]
    assert detail_calls == []

    # Case 3: #2 handle probe misses -> falls back to #1 /api/user/detail/ bioLink and resolves email
    monkeypatch.setattr(tiktokapi_source, "probe_handle_bio_links", lambda handle, **kw: [])
    monkeypatch.setattr(
        tiktokapi_source,
        "emails_from_bio_links",
        lambda bio, links, **kw: (link_fetches.append(links), ["detail@gmail.com"])[1],
    )
    profile3 = asyncio.run(
        src._profile_from_videos(niche="fitness", handle="detail.fit", followers=50_000, bio="Online fitness coach 👇", feed_hashtags=set())
    )
    assert profile3.extra_emails == ["detail@gmail.com"]
    assert detail_calls == [("https://www.tiktok.com/api/user/detail/", "detail.fit")]
    assert link_fetches == [["https://linktr.ee/custom_coach_page"]]

    # Case 4: _profile_from_user (used by --enrich-no-email / recheck) also runs #2 -> #1 after embed qualifies
    detail_calls.clear()
    link_fetches.clear()
    monkeypatch.setattr(
        tiktokapi_source,
        "emails_from_bio_links",
        lambda bio, links, **kw: (link_fetches.append(links), ["detail@gmail.com"])[1] if links else [],
    )
    monkeypatch.setattr(
        tiktokapi_source,
        "fetch_embed",
        lambda handle: EmbedProfile(
            handle=handle,
            followers=50_000,
            bio="Online fitness coach 👇",
            views=[30_000, 40_000, 35_000],
            captions=["leg day squats"],
            video_ids=["1"],
        ),
    )
    profile4 = asyncio.run(src._profile_from_user("detail.fit", niche="fitness"))
    assert profile4.extra_emails == ["detail@gmail.com"]
    assert detail_calls == [("https://www.tiktok.com/api/user/detail/", "detail.fit")]
    assert link_fetches == [["https://linktr.ee/custom_coach_page"]]


