from tiktok_leads.filters import evaluate_candidate
from tiktok_leads.models import CandidateProfile
from tiktok_leads.niches import is_harvestable_hashtag


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
    captions = ["leg day", "new deadlift cue", "8 weeks out, 510kg total", "squat PR", "meet prep"]
    assert evaluate("fitness", "", captions).lead is not None


def test_gym_hashtags_alone_do_not_count():
    captions = ["song name in comments #gym #gymtok", "drop soon #gymmusic", "#gymmotivation #workout", "#fitness"]
    assert evaluate("fitness", "1 million+ monthly on Spotify!", captions).skip_code == "off_niche"


def test_recipe_handbag_and_dog_training_pages_are_off_niche():
    recipes = ["gluten free protein cookie dough", "gluten free balls", "30g protein breakfast", "vegan protein pasta"]
    assert evaluate("fitness", "vegan recipes, vlogs", recipes).skip_code == "off_niche"
    bags = ["Coach outlet haul", "my Coach tabby collection", "Coach bag unboxing", "new Coach purse"]
    assert evaluate("fitness", "bags and outfits", bags).skip_code == "off_niche"
    dogs = ["Training with the Commander!", "puppy training day 2", "training my husky", "Baby and puppy playing"]
    assert evaluate("fitness", "animals and babies", dogs).skip_code == "off_niche"


def test_fitness_bio_needs_one_fitness_caption():
    assert evaluate("fitness", "Online Coach, fat loss", ["", "", ""]).skip_code == "off_niche"
    assert evaluate("fitness", "Online Coach, fat loss", ["", "new workout split", ""]).lead is not None


def test_fashion_and_event_words_do_not_count():
    captions = ["fall outfit check", "coachella day 1", "lash lift results", "#footballtraining drills"]
    assert evaluate("fitness", "", captions).skip_code == "off_niche"


def test_niche_without_a_word_list_is_not_gated():
    assert evaluate("mom", "", ["cat update", "", ""]).lead is not None


def test_only_gym_hashtags_are_crawled_for_fitness():
    for tag in ("gymtok", "fitnesscoach", "weightlifting", "personaltrainer", "glutegrowth", "backworkout"):
        assert is_harvestable_hashtag(tag, "fitness"), tag
    for tag in ("fitjeans", "officefits", "dogtraining", "lashliftandtint", "hauslabs", "glutenfree", "beachyoga"):
        assert not is_harvestable_hashtag(tag, "fitness"), tag
    # Food, sport and family tags reach creators reviewers reject, even with a gym word in them.
    for tag in ("gymrecipes", "proteinpancakes", "runnerworkout", "hyroxtraining", "fitmom", "crossfitgames"):
        assert not is_harvestable_hashtag(tag, "fitness"), tag
    for tag in ("gymmomentum", "maximumgym", "crunchworkout"):
        assert is_harvestable_hashtag(tag, "fitness"), tag


def test_groups_reviewers_reject_are_named():
    from tiktok_leads.niches import rejected_group

    gym = ["Leg day with heavy squats", "My top 3 tricep exercises", "Glute workout"]
    assert rejected_group("fitness", "Online fitness coach", gym, "coach@gmail.com") is None
    assert rejected_group("fitness", "Online fitness coach", gym, "coach@hotmail.co.uk") == "email on a country domain"
    assert rejected_group("fitness", "Fitness | mom of 3", gym, "a@gmail.com") == "family or lifestyle"
    assert rejected_group("fitness", "Gym and marathon training", gym, "a@gmail.com") == "sport"
    recipes = ["High protein recipe", "Easy dinner for the week", "Leg day"]
    assert rejected_group("fitness", "Gym girl", recipes, "a@gmail.com") == "food"
    # One food caption is not enough, and words inside other words do not count.
    assert rejected_group("fitness", "Momentum fitness, summa cum laude", gym + ["My dinner"], "a@gmail.com") is None
    assert rejected_group("mom", "mom of 3", recipes, "a@x.co.uk") is None
