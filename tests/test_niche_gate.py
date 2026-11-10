from tiktok_leads.filters import evaluate_candidate
from tiktok_leads.models import CandidateProfile


def evaluate(niche: str, bio: str, captions: list[str]):
    return evaluate_candidate(
        CandidateProfile(
            handle="creator",
            profile_url="https://www.tiktok.com/@creator",
            niche=niche,
            followers_count=50_000,
            recent_video_views=[30_000] * len(captions),
            recent_video_captions=captions,
            bio=f"{bio} creator@example.org",
        ),
        min_followers=10_000,
        min_average_views=10_000,
    )


def test_creator_with_one_fitness_video_is_off_niche():
    captions = ["first time trying #crossfit", "The sea witch!!", "Ursula spotted!", "new corset", "cat update"]
    assert evaluate("fitness", "Maximalist Crazy Cat Lady", captions).skip_code == "off_niche"


def test_gym_captions_make_a_fitness_lead():
    captions = ["leg day", "#gym #powerlifting", "8 weeks out, 510kg total", "squat PR", "meet prep"]
    assert evaluate("fitness", "", captions).lead is not None


def test_fitness_bio_needs_one_fitness_caption():
    assert evaluate("fitness", "Online Coach, fat loss", ["", "", ""]).skip_code == "off_niche"
    assert evaluate("fitness", "Online Coach, fat loss", ["", "new workout split", ""]).lead is not None


def test_fashion_and_event_words_do_not_count():
    captions = ["fall outfit check", "coachella day 1", "lash lift results", "#footballtraining drills"]
    assert evaluate("fitness", "", captions).skip_code == "off_niche"


def test_niche_without_a_word_list_is_not_gated():
    assert evaluate("mom", "", ["cat update", "", ""]).lead is not None
