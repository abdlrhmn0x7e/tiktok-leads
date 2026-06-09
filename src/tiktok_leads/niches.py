from __future__ import annotations

NICHE_HASHTAGS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "fittok",
        "gymtok",
        "fitnesstok",
        "gymlife",
        "gymmotivation",
        "workout",
        "workoutroutine",
        "workoutmotivation",
        "homeworkout",
        "strengthtraining",
        "weightloss",
        "fatloss",
        "musclebuilding",
        "bodybuilding",
        "calisthenics",
        "pilates",
        "yogatiktok",
        "runningtok",
        "mealprep",
        "nutritiontips",
        "personaltrainer",
        "fitnesscoach",
        "beginnergymworkout",
        "fitnessadvice",
    ),
    "mom": (
        "momtok",
        "momlife",
        "momsoftiktok",
        "motherhood",
        "moms",
        "sahm",
        "sahmlife",
        "workingmom",
        "newmom",
        "firsttimemom",
        "postpartum",
        "pregnancytok",
        "parenttok",
        "fitmom",
        "momfitness",
        "momroutine",
        "momvlog",
        "familyvlog",
        "babyproducts",
        "toddlerlife",
        "parentingtips",
        "familytime",
        "toddlermom",
    ),
    "lifestyle": (
        "lifestyle",
        "lifestyleblogger",
        "dayinmylife",
        "dailylife",
        "morningroutine",
        "nightroutine",
        "selfcare",
        "selfcaretips",
        "wellness",
        "wellnesstok",
        "mindfulness",
        "aesthetic",
        "homedecor",
        "organization",
        "healthyhabits",
        "skincareroutine",
        "travelvlog",
        "productivitytips",
    ),
}


# Free-text phrases for the user-search discovery vector (api.search.users).
# These surface creators that the hashtag feed never shows.
NICHE_SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "fitness coach",
        "personal trainer",
        "online fitness coach",
        "weight loss coach",
        "fitness nutritionist",
        "home workout coach",
    ),
    "mom": (
        "mom coach",
        "mom blogger",
        "motherhood content creator",
        "parenting coach",
        "pregnancy coach",
        "mompreneur",
    ),
    "lifestyle": (
        "lifestyle blogger",
        "wellness coach",
        "self care creator",
        "morning routine creator",
        "lifestyle content creator",
        "mindfulness coach",
    ),
}


def hashtags_for_niche(niche: str) -> tuple[str, ...]:
    return NICHE_HASHTAGS.get(niche.strip().lower(), ())


def search_terms_for_niche(niche: str) -> tuple[str, ...]:
    return NICHE_SEARCH_TERMS.get(niche.strip().lower(), ())
