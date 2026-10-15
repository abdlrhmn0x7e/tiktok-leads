from __future__ import annotations

# Tag lists were pruned on 2026-09-18 using leads-hub review data: for "mom",
# identity/vlog tags (#momvlog, #sahmlife, #girlmom) were accepted 80-95% of
# the time while advice/coach tags (#parentingtips, #momcoach, #momtips) fell
# under 50%. For "lifestyle", routine/wellness creator tags converted at 50-80%
# while product/home tags (#homedecor, #targetfinds, #wellness) sat at 12-33%.
#
# Refreshed on 2026-10-12. TikTok's hashtag feed only serves a few hundred
# creators before its cursor wraps, so seed tags drain: from 2026-10-09 on,
# most fitness/mom seeds yielded 0-1 new leads. Tags that still yielded 2+ were
# kept; drained ones were swapped for tags from discovered_hashtags that
# qualifying creators use often and that are niche-exclusive (>=85% of
# volume-normalized mentions in this niche for fitness/mom, >=78% for the
# fuzzier lifestyle/grwm). Cross-niche tags (#morningroutine, #dayinmylife,
# #mealprep, #skincareroutine, #fitmom, #ugccreator) were dropped: handles
# are deduped globally, so a shared tag just makes niches steal each other's
# creators. #grwm/#getreadywithme stay as the grwm niche's anchor tags.
NICHE_HASHTAGS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "fitnessjourney",
        "fitnessinfluencer",
        "weightlossjourney",
        "gymtok",
        "backworkout",
        "backday",
        "legworkout",
        "gluteworkout",
        "glutegrowth",
        "absworkout",
        "abworkout",
        "armworkout",
        "biceps",
        "upperbody",
        "beginnerworkout",
        "gymbeginner",
        "progressiveoverload",
        "hypertrophy",
        "weightlifting",
        "powerlifting",
        "crossfit",
        "hyrox",
        "hybridathlete",
        "marathontraining",
        "mobility",
        "fatloss",
        "fatlosstips",
        "weightlosstips",
        "gymtransformation",
        "dietitian",
    ),
    "mom": (
        "momtok",
        "momsoftiktok",
        "motherhood",
        "firsttimemom",
        "toddlermom",
        "millennialmom",
        "workingmom",
        "parenttok",
        "familycontentcreator",
        "toddlerlife",
        "momadvice",
        "momvlog",
        "boymom",
        "dayinthelifemom",
        "newbornlife",
        "sahm",
        "stayathomemom",
        "youngmom",
        "momsover30",
        "mumlife",
        "momlifebelike",
        "relatablemom",
        "realisticmomlife",
        "motherhoodunfiltered",
        "momcontent",
        "momhack",
        "momoftwo",
        "momof4",
        "2under2",
        "homeschool",
        "parentsoftiktok",
        "babiesoftiktok",
        "babytok",
        "toddlertok",
        "newbornbaby",
        "babymusthaves",
        "pregnanttiktok",
        "pregnancytiktok",
        "laboranddelivery",
        "csection",
        "postpartumrecovery",
    ),
    "lifestyle": (
        "lifestyleblogger",
        "lifestyleinfluencer",
        "lifestylecoach",
        "mindfulness",
        "mindfulnesscoach",
        "mindsetcoach",
        "holistichealth",
        "nontoxicliving",
        "cleanliving",
        "intentionalliving",
        "minimalistlifestyle",
        "personaldevelopment",
        "habittracking",
        "routinevlog",
        "minimalism",
        "minimalist",
        "meditation",
        "lifecoach",
        "spirituality",
        "lowtoxliving",
        "cottagecore",
    ),
    "grwm": (
        "grwm",
        "getreadywithme",
        "grwmforwork",
        "grwmforschool",
        "grwmchitchat",
        "chitchatgrwm",
        "morningmakeup",
        "ugcbeauty",
        "beautyugc",
        "makeupgrwm",
        "makeuptutorial",
        "eyeshadowtutorial",
        "makeuptips",
        "makeuphack",
        "makeuplook",
        "makeupartist",
        "fullglam",
        "softglam",
        "fallmakeup",
        "halloweenmakeup",
        "beautycontentcreator",
    ),
}


# Trending topics the client asked for (2026-10-15): 25-50% of daily outreach
# should be creators whose content pillars are one of these, the rest general.
# They live under existing niches so leads keep a label the outreach form
# accepts; the daemon schedules them at DAEMON_TRENDING_SHARE of each sweep.
# Each tag sits in exactly one niche, for the same dedupe reason as above.
TRENDING_HASHTAGS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "macrofriendlyswaps",
        "macrofriendly",
        "proffee",
        "proteincoffee",
        "volumeeating",
        "caloriedeficithack",
        "lazyfitness",
        "aestheticeats",
        "hiddenmacros",
        "tinnedfish",
        "cottagecheese",
        "cottagecheeserecipes",
        "highprotein",
        "proteinintake",
        "proteinmaxxing",
        "fibermaxxing",
        "guthealth",
        "guttok",
    ),
    "lifestyle": (
        "mushroomcoffee",
        "habitstacking",
        "healthyhabits",
        "holistichealing",
        "holisticwellness",
        "hormonehealth",
        "wellnesstrends",
    ),
}

TRENDING_SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "high protein recipes",
        "macro friendly recipes",
        "volume eating",
        "protein coffee",
        "gut health",
    ),
    "lifestyle": (
        "holistic health coach",
        "hormone health",
        "habit stacking",
        "wellness trends 2027",
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
        "fat loss coach",
        "fitness nutritionist",
        "nutrition coach",
        "strength coach",
        "pilates instructor",
        "home workout coach",
    ),
    "mom": (
        "mom blogger",
        "mom influencer",
        "mom content creator",
        "mompreneur",
        "motherhood content creator",
        "parenting coach",
        "gentle parenting coach",
        "pregnancy coach",
        "postpartum coach",
    ),
    "lifestyle": (
        "lifestyle blogger",
        "lifestyle influencer",
        "lifestyle content creator",
        "ugc creator",
        "micro influencer",
        "wellness coach",
        "self care creator",
        "morning routine creator",
        "mindfulness coach",
        "product review creator",
    ),
    "grwm": (
        "grwm",
        "get ready with me",
        "beauty creator",
        "beauty influencer",
        "makeup content creator",
        "skincare creator",
        "ugc beauty creator",
    ),
}


# Engagement-bait tags every creator stamps on every video. Harvesting these
# would crawl the generic For You firehose and fill the niche with unrelated
# accounts (this is how a monkey account ended up tagged "mom").
# Seed tags in NICHE_HASHTAGS bypass this list, so the "grwm" niche still
# crawls #grwm while other niches never harvest it.
GENERIC_HASHTAGS: frozenset[str] = frozenset(
    {
        "fyp",
        "fypage",
        "fypシ",
        "fypシ゚viral",
        "foryou",
        "foryoupage",
        "fy",
        "viral",
        "viralvideo",
        "viralvideos",
        "viraltiktok",
        "virall",
        "trending",
        "trend",
        "explore",
        "explorepage",
        "xyzbca",
        "tiktok",
        "tiktokviral",
        "tiktokfamous",
        "duet",
        "stitch",
        "greenscreen",
        "capcut",
        "capcutedit",
        "funny",
        "funnyvideos",
        "comedy",
        "meme",
        "memes",
        "lol",
        "relatable",
        "pov",
        "storytime",
        "grwm",
        "ootd",
        "vlog",
        "vlogger",
        "vlogtok",
        "minivlog",
        "ad",
        "ads",
        "sponsored",
        "gifted",
        "commission",
        "asmr",
        "satisfying",
        "oddlysatisfying",
        "aesthetic",
        "cute",
        "love",
        "happy",
        "life",
        "video",
        "videos",
        "new",
        "follow",
        "like",
        "likes",
        "share",
        "real",
        "usa",
        "us",
        "uk",
    }
)


def is_harvestable_hashtag(tag: str) -> bool:
    """Worth feeding into the discovery flywheel: long enough to mean
    something, not generic engagement bait, not just digits."""
    tag = tag.strip().lower()
    return len(tag) >= 3 and not tag.isdigit() and tag not in GENERIC_HASHTAGS


def hashtags_for_niche(niche: str) -> tuple[str, ...]:
    return NICHE_HASHTAGS.get(niche.strip().lower(), ())


def search_terms_for_niche(niche: str) -> tuple[str, ...]:
    return NICHE_SEARCH_TERMS.get(niche.strip().lower(), ())


def trending_hashtags_for_niche(niche: str) -> tuple[str, ...]:
    return TRENDING_HASHTAGS.get(niche.strip().lower(), ())


def trending_search_terms_for_niche(niche: str) -> tuple[str, ...]:
    return TRENDING_SEARCH_TERMS.get(niche.strip().lower(), ())


def trending_found_via() -> set[str]:
    """scraped_profiles.found_via values that mean "found through a trending
    topic", for measuring the trending share of leads."""
    tags = {f"#{tag}" for tags in TRENDING_HASHTAGS.values() for tag in tags}
    terms = {f"search:{term}" for terms in TRENDING_SEARCH_TERMS.values() for term in terms}
    return tags | terms
