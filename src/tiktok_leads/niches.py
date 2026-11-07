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


# Fallback niche for leads-hub search terms (fetched each sweep from Convex)
# that have no niche of their own. Must match SEARCH_NICHE in leads-hub's
# convex/searchTerms.ts.
SEARCH_NICHE = "search"


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


# leads-hub review data (2026-11), fitness leads grouped by the hashtag that
# first surfaced them: each of these had >=70% of reviewed leads rejected
# (n>=8), against 0-25% for workout/coach tags (#gluteworkout 2.5% of n=40,
# #legday 6%, #fitnesscoach 0%). They are never crawled, whether seed,
# trending, or harvested. Trending ones stay listed in TRENDING_HASHTAGS so
# --stats still attributes past leads to them.
REJECTED_HASHTAGS: dict[str, frozenset[str]] = {
    "fitness": frozenset(
        {
            "aestheticeats",
            "cottagecheese",
            "dietitian",
            "fit",
            "food",
            "glowup",
            "guttok",
            "healthy",
            "highprotein",
            "makeup",
            "outfitinspo",
            "proteincoffee",
            "tinnedfish",
            "transformation",
            "travel",
            "whatieatinaday",
        }
    ),
}

# A harvested hashtag must contain one of its niche's stems: off-niche tags
# (#makeup, #skincare, #food, #creatorsearchinsights) were the most-mentioned
# harvested fitness tags and pulled in creators reviewers reject.
HARVEST_STEMS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "gym", "fit", "workout", "lift", "train", "muscle", "glute", "abs", "legday",
        "bicep", "pilates", "yoga", "calisthenic", "bodybuild", "powerlift", "crossfit",
        "hyrox", "marathon", "running", "runner", "strength", "cardio", "hiit",
        "weightloss", "fatloss", "protein", "macro", "calorie", "mealprep", "nutrition",
        "coach",
    ),
}


def is_harvestable_hashtag(tag: str, niche: str | None = None) -> bool:
    """Worth feeding into the discovery flywheel: long enough to mean
    something, not generic engagement bait, not just digits, not rejected by
    reviewers, and on-niche when `niche` has stems."""
    tag = tag.strip().lower()
    stems = HARVEST_STEMS.get((niche or "").strip().lower())
    # "outfit" contains the "fit" stem but is fashion, not fitness.
    on_niche = not stems or any(stem in tag.replace("outfit", "") for stem in stems)
    return (
        len(tag) >= 3
        and not tag.isdigit()
        and tag not in GENERIC_HASHTAGS
        and tag not in REJECTED_HASHTAGS.get((niche or "").strip().lower(), ())
        and on_niche
    )


def hashtags_for_niche(niche: str) -> tuple[str, ...]:
    return _crawlable(niche, NICHE_HASHTAGS)


def search_terms_for_niche(niche: str) -> tuple[str, ...]:
    return NICHE_SEARCH_TERMS.get(niche.strip().lower(), ())


def trending_hashtags_for_niche(niche: str) -> tuple[str, ...]:
    return _crawlable(niche, TRENDING_HASHTAGS)


def _crawlable(niche: str, lists: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    key = niche.strip().lower()
    rejected = REJECTED_HASHTAGS.get(key, frozenset())
    return tuple(tag for tag in lists.get(key, ()) if tag not in rejected)


def trending_search_terms_for_niche(niche: str) -> tuple[str, ...]:
    return TRENDING_SEARCH_TERMS.get(niche.strip().lower(), ())


def trending_found_via() -> set[str]:
    """scraped_profiles.found_via values that mean "found through a trending
    topic", for measuring the trending share of leads."""
    tags = {f"#{tag}" for tags in TRENDING_HASHTAGS.values() for tag in tags}
    terms = {f"search:{term}" for terms in TRENDING_SEARCH_TERMS.values() for term in terms}
    return tags | terms
