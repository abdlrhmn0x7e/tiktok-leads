from __future__ import annotations

NICHE_HASHTAGS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "fittok",
        "gymtok",
        "fitnesstok",
        "gymlife",
        "gymmotivation",
        "workout",
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
        "fitmom",
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
        "wellnesstok",
        "mindfulness",
        "productivitytips",
    ),
}


def hashtags_for_niche(niche: str) -> tuple[str, ...]:
    return NICHE_HASHTAGS.get(niche.strip().lower(), ())
