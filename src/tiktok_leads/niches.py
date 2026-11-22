from __future__ import annotations

import re
from collections.abc import Sequence

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
        "gymtok",
        "gymrat",
        "bodybuilding",
        "strengthtraining",
        "backworkout",
        "backday",
        "chestday",
        "legday",
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
        "squats",
        "deadlift",
        "benchpress",
        "fatloss",
        "fatlosstips",
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
            "caloriedeficithack",
            "cottagecheese",
            "cottagecheeserecipes",
            "dietitian",
            "fibermaxxing",
            "fit",
            "food",
            "glowup",
            "guthealth",
            "guttok",
            "healthy",
            "hiddenmacros",
            "highprotein",
            "macrofriendly",
            "macrofriendlyswaps",
            "makeup",
            "outfitinspo",
            "proffee",
            "proteincoffee",
            "proteinintake",
            "proteinmaxxing",
            "tinnedfish",
            "transformation",
            "travel",
            "volumeeating",
            "whatieatinaday",
        }
    ),
}


# Yahoo web-search phrases, each run as `site:tiktok.com <phrase>`. Results
# show the creator's bio email and follower count in the snippet, so hits are
# pre-filtered before any TikTok request. Gmail phrases yielded ~2.5 new usable
# creators per query; "collab"/"brand deals" without a domain yielded ~0.1.
# Non-gym phrases (running, marathon, hyrox, crossfit, meal prep, recipes,
# dietitian, yoga, triathlon, boxing, fit mom, postpartum) were removed so the
# 14-day cursor reset only refreshes gym/strength/personal-training queries.
YAHOO_QUERIES: dict[str, tuple[str, ...]] = {
    "fitness": (
        '"gmail.com" "personal trainer"',
        '"gmail.com" "fitness coach"',
        '"gmail.com" "online coach" gym',
        '"gmail.com" "online fitness coach"',
        '"gmail.com" "NASM"',
        '"gmail.com" "ISSA" trainer',
        '"gmail.com" bodybuilding',
        '"gmail.com" "bodybuilding coach"',
        '"gmail.com" "powerlifting"',
        '"gmail.com" "pilates"',
        '"gmail.com" "calisthenics"',
        '"gmail.com" "weight loss" coach',
        '"collabs" "gmail.com" fitness',
        '"gmail.com" "glute"',
        '"gmail.com" "glute growth"',
        '"gmail.com" "strength coach"',
        '"gmail.com" "fat loss" gym',
        '"gmail.com" "glute workout"',
        '"outlook.com" "personal trainer"',
        '"gmail.com" "pilates instructor"',
        '"gmail.com" "weight loss coach"',
        '"gmail.com" "home workout"',
        '"gmail.com" "fitness journey"',
        '"gmail.com" "bodybuilder"',
        '"gmail.com" "gymtok"',
        '"gmail.com" "strength training"',
        '"gmail.com" "gym girl"',
        '"gmail.com" "fitness influencer"',
        '"gmail.com" "workout routine"',
        '"gmail.com" "online personal trainer"',
        '"gmail.com" "certified personal trainer"',
        '"gmail.com" "leg day"',
        '"gmail.com" "chest day"',
        '"gmail.com" "back day"',
        '"gmail.com" "push pull legs"',
        '"gmail.com" "hypertrophy"',
        '"gmail.com" "fitness model"',
        '"gmail.com" "physique"',
        '"gmail.com" "bikini competitor"',
        '"gmail.com" "calorie deficit" gym',
        '"gmail.com" "kettlebell"',
        '"gmail.com" "lifting" gym',
        '"gmail.com" "booty workout"',
        '"outlook.com" "fitness coach"',
        '"outlook.com" "gym"',
        '"outlook.com" "workout"',
        '"outlook.com" "glute"',
        '"outlook.com" "weight loss"',
        '"outlook.com" "bodybuilding"',
        '"icloud.com" "gym"',
        '"icloud.com" "fitness"',
        '"icloud.com" "personal trainer"',
        '"icloud.com" "workout"',
        '"icloud.com" "lifting" gym',
        '"yahoo.com" "gym"',
        '"yahoo.com" "fitness"',
        '"yahoo.com" "personal trainer"',
        '"yahoo.com" "workout"',
        '"yahoo.com" "lifting" gym',
        '"hotmail.com" "gym"',
        '"hotmail.com" "fitness"',
        '"hotmail.com" "workout"',
        '"gmail.com" "gym" "business inquiries"',
        '"gmail.com" "gym" "inquiries"',
        '"gmail.com" "gym" "collab"',
        '"gmail.com" "fitness" "inquiries"',
        '"gmail.com" "fitness" "collab"',
        '"gmail.com" "fitness coach" "inquiries"',
        '"gmail.com" "workout" "business inquiries"',
        '"gmail.com" "workout" "inquiries"',
        '"gmail.com" "gym rat"',
        '"gmail.com" "gym bro"',
        '"gmail.com" "bulking"',
        '"gmail.com" "online coaching" gym',
        '"gmail.com" "IFBB"',
        '"gmail.com" "weightlifting"',
        '"gmail.com" "squat" gym',
        '"gmail.com" "bench press"',
        '"gmail.com" "deadlift"',
        '"gmail.com" "fitness tips"',
        '"gmail.com" "gym motivation"',
        '"gmail.com" "powerlifter"',
        '"gmail.com" "gymshark"',
        '"gmail.com" "dfyne"',
        '"gmail.com" "aybl"',
        '"gmail.com" "youngla"',
        '"gmail.com" "fitness creator"',
        '"gmail.com" "home gym"',
        '"gmail.com" "CPT" gym',
        '"gmail.com" "gym content"',
        '"gmail.com" "gym wear"',
    ),
}

# A harvested hashtag must contain one of its niche's stems: off-niche tags
# (#makeup, #skincare, #food, #creatorsearchinsights) were the most-mentioned
# harvested fitness tags and pulled in creators reviewers reject. "coach" and
# bare "trainer" are not stems: they crawled #coachoutlet, #coachella,
# #horsetrainersoftiktok, and #dogtrainer. Bare "fit", "train", "lift", "abs",
# "yoga", "calorie", and "marathon" are excluded too.
HARVEST_STEMS: dict[str, tuple[str, ...]] = {
    "fitness": (
        "gym", "fitness", "fittok", "fitfam", "workout", "lifting", "weightlift", "deadlift",
        "muscle", "glute", "legday", "bicep", "pilates", "calisthenic", "bodybuild",
        "powerlift", "strength", "hiit",
        "weightloss", "fatloss", "macro", "caloriedeficit", "nutrition",
        # Lift, split, and explicit gym-coaching stems.
        "squat", "benchpress", "chestday", "armday", "shoulderday", "backday", "pushday",
        "pullday", "pushpull", "physique", "bulking", "leanbulk", "kettlebell", "dumbbell",
        "barbell", "tricep", "hamstring", "smithmachine", "cablemachine", "strongwom",
        "stronggirl", "strongnotskinny", "gains", "wholift", "weightedvest", "onlinecoach",
        "personaltrain", "fitnesscoach", "gymcoach", "strengthcoach",
    ),
}

# Tags with a food, sport, family, animal-training, or beauty/gymnastics word
# feed creators `rejected_group` skips anyway.
HARVEST_EXCLUDED: dict[str, re.Pattern[str]] = {
    "fitness": re.compile(
        r"recipe|meal|food|cook|bake|snack|breakfast|dinner|lunch|vegan|keto|whatieat|protein"
        r"|running|runner|marathon|hyrox|crossfit|boxing|football|soccer|triathl"
        r"|gymnast|yoga|softball|baseball|tennis|volleyball|golf|hockey|pickleball"
        r"|mom(?!ent)|(?<!maxi)mum|baby|pregnan|postpartum|dad(?!dy)|papa"
        r"|horse|equestrian|dog|canine|puppy|lash|salon|pilatesprincess"
        r"|bargain|drsquatch"
    ),
}


def is_harvestable_hashtag(tag: str, niche: str | None = None) -> bool:
    """Worth feeding into the discovery flywheel: long enough to mean
    something, not generic engagement bait, not just digits, not rejected by
    reviewers, and on-niche when `niche` has stems."""
    tag = tag.strip().lower()
    stems = HARVEST_STEMS.get((niche or "").strip().lower())
    # "gluten" contains the "glute" stem but is food, not fitness.
    on_niche = not stems or any(stem in tag.replace("gluten", "") for stem in stems)
    excluded = HARVEST_EXCLUDED.get((niche or "").strip().lower())
    if excluded is not None and excluded.search(tag):
        return False
    return (
        len(tag) >= 3
        and not tag.isdigit()
        and tag not in GENERIC_HASHTAGS
        and tag not in REJECTED_HASHTAGS.get((niche or "").strip().lower(), ())
        and on_niche
    )


# Words a gym or strength-training creator uses in their own bio and captions.
# A creator who posted one video under a fitness hashtag is usually not one:
# reviewers rejected 85% of the leads pushed 2026-11-05..10. Running, yoga,
# crossfit, hyrox, bare "protein", "training", "trainer", "coach", "coaching",
# "athlete", and "calories" are left out because recipe, horse/dog training,
# dating/mindset coaching, lash-lift, gymnastics, and non-gym sport pages passed
# on them. Gymwear brand tags (gymshark/dfyne/aybl/youngla/etc.) capture gym
# creators whose bios only list ambassador codes.
CONTENT_TERMS: dict[str, re.Pattern[str]] = {
    "fitness": re.compile(
        r"(?i)(?<![a-z])(?:"
        r"gym(?!(?:nast|nastic))[a-z]*|fitness\w*|fittok|fitfam|workouts?|work ?out"
        r"|(?<!lash )(?<!face )(?<!brow )(?<!fork)lift(?:ing|er|s)?"
        r"|powerlift\w*|weightlift\w*|body ?build\w*|physique"
        r"|personal train\w*|online (?:fitness )?coach\w*"
        r"|(?:fitness|strength|gym|physique|body ?recomp|prep) coach\w*|certified pt|cpt"
        r"|pilates|calisthenics\w*|strength\w*|muscles?\w*|glute(?!n)\w*|abs"
        r"|leg ?day|chest ?day|back ?day|arm ?day|shoulder ?day|push ?day|pull ?day"
        r"|squats?|deadlift\w*|bench(?:press)?|hypertrophy|bulk(?:ing)?|shred\w*|gains|macros?"
        r"|calorie ?deficit|fat ?loss|weight ?loss\w*|ifbb|cardio|hiit|mobility|nutrition\w*"
        r"|barbell|dumbbell|kettlebell|smith ?machine|triceps?|biceps?|hamstrings?"
        r"|dfyne|aybl|youngla|alphalete|oner ?active|teveo|nvgtn|ekkovision"
        r")(?![a-z])"
    ),
}

# Stricter bio signal for promoting a followed creator to a level-2 following
# seed: requires an explicit gym/lifting/fitness-coaching signal (not bare
# nutrition, macros, mobility, cardio, or weight loss), preventing off-niche
# snowball drift.
SEED_BIO_TERMS: dict[str, re.Pattern[str]] = {
    "fitness": re.compile(
        r"(?i)(?<![a-z])(?:"
        r"gym(?!(?:nast|nastic))[a-z]*|fitness\w*|fittok|fitfam|workouts?|work ?out"
        r"|(?<!lash )(?<!face )(?<!brow )(?<!fork)lift(?:ing|er|s)?"
        r"|powerlift\w*|weightlift\w*|body ?build\w*|physique"
        r"|personal train\w*|online (?:fitness )?coach\w*"
        r"|(?:fitness|strength|gym|physique|body ?recomp|prep) coach\w*|certified pt|cpt"
        r"|strength\w*|muscles?\w*|glute(?!n)\w*|leg ?day|chest ?day|back ?day"
        r"|squats?|deadlift\w*|bench(?:press)?|hypertrophy|ifbb"
        r"|barbell|dumbbell|kettlebell|calisthenics\w*"
        r"|dfyne|aybl|youngla|alphalete|oner ?active|teveo|nvgtn|ekkovision"
        r")(?![a-z])"
    ),
}


def is_on_niche(niche: str, bio: str, captions: Sequence[str]) -> bool:
    """Whether the creator's own content is about `niche`: three recent
    captions use its words, or the bio and one caption do. Hashtags in
    captions do not count: music and edit pages tag every video #gym.
    Niches without a word list always pass."""
    terms = CONTENT_TERMS.get(niche.strip().lower())
    if terms is None:
        return True
    hits = sum(bool(terms.search(re.sub(r"#\S+", "", caption))) for caption in captions)
    return hits >= 3 or (hits >= 1 and bool(terms.search(bio)))


# Groups reviewers kept under 16% of, against 28% overall (324 reviewed leads,
# 2026-11-17..18), plus non-gym professions/promos found in the Nov 21 audit.
_SPORT = (
    r"run|running|runners?|marathon\w*|hyrox|crossfit|boxing|mma|football|soccer|basketball"
    r"|cycling|triathl\w*|softball|baseball|tennis|volleyball|golf|hockey|pickleball"
    r"|gymnast(?:ics?|s)?|yoga|yogi|wrestling|bjj|jiu[- ]?jitsu|cheer(?:leading)?"
)
_FOOD = (
    r"recipes?|meal ?prep|protein (?:balls?|bars?|shakes?|pancakes?)|cook\w*|bak(?:e|ed|ing)"
    r"|ingredients?|delicious|tasty|breakfast|dinner|snacks?"
)
_FAMILY = (
    r"mom(?:s|my|mies|life)?|mum(?:s|my)?|dad(?:s|dy)?|papa|father(?:hood)?|toddlers?|baby|husband|wife"
    r"|vlogs?|grwm|day in my life|outfits?|makeup|skincare|lash(?:es| ?lift)?|salon|medspa"
    r"|natural hair|pop culture|skits?"
)
_NON_GYM = (
    r"dog ?train\w*|canine|k9|pupp(?:y|ies)|horse ?train\w*|equestrian|stable"
    r"|songs? ?promo|dm for (?:songs? )?promo|spotify|telegram|kik|promotions"
    r"|relationship coach\w*|dating coach\w*|avoidant|psycholog(?:y|ist)|therapist"
    r"|biohacking|longevity|reiki"
)
REJECTED_GROUPS: dict[str, dict[str, re.Pattern[str]]] = {
    "fitness": {
        "sport": re.compile(rf"(?i)(?<![a-z])(?:{_SPORT})(?![a-z])"),
        "food": re.compile(rf"(?i)(?<![a-z])(?:{_FOOD})(?![a-z])"),
        "family or lifestyle": re.compile(rf"(?i)(?<![a-z])(?:{_FAMILY})(?![a-z])"),
        "non-gym business or promo": re.compile(rf"(?i)(?<![a-z])(?:{_NON_GYM})(?![a-z])"),
    },
}
_COUNTRY_EMAIL = re.compile(
    r"(?i)\.(?:uk|de|nl|fr|pl|es|it|au|ie|se|dk|no|cz|be|at|ch|br|mx"
    r"|nz|za|in|pt|fi|ro|hu|sk|hr|bg|gr|tr|ru|ua|ar|cl|pe|sg|my|ph|id|th|vn|ae|sa|il|ng|ke|pk|jp|kr|cn)$"
)


def rejected_group(niche: str, bio: str, captions: Sequence[str], email: str) -> str | None:
    """The group reviewers reject this creator with, if any: a sport, food,
    family, or non-gym word in the bio, a sport/food/non-gym word in two recent
    captions, or an email on a country domain."""
    groups = REJECTED_GROUPS.get(niche.strip().lower())
    if groups is None:
        return None
    if email and _COUNTRY_EMAIL.search(email.strip()):
        return "email on a country domain"
    texts = [re.sub(r"#\S+", "", caption) for caption in captions]
    for name, terms in groups.items():
        in_captions = name != "family or lifestyle" and sum(bool(terms.search(text)) for text in texts) >= 2
        if terms.search(bio) or in_captions:
            return name
    return None


def bio_on_niche(niche: str, bio: str) -> bool:
    """Whether the bio uses `niche`'s words. Niches without a word list pass."""
    terms = CONTENT_TERMS.get(niche.strip().lower())
    return terms is None or bool(terms.search(bio))


def seed_bio_on_niche(niche: str, bio: str) -> bool:
    """Stricter bio check for promoting a followed account to a following seed."""
    key = niche.strip().lower()
    terms = SEED_BIO_TERMS.get(key, CONTENT_TERMS.get(key))
    return terms is None or bool(terms.search(bio))


def hashtags_for_niche(niche: str) -> tuple[str, ...]:
    return _crawlable(niche, NICHE_HASHTAGS)


def search_terms_for_niche(niche: str) -> tuple[str, ...]:
    return NICHE_SEARCH_TERMS.get(niche.strip().lower(), ())


def yahoo_queries_for_niche(niche: str) -> tuple[str, ...]:
    return YAHOO_QUERIES.get(niche.strip().lower(), ())


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
