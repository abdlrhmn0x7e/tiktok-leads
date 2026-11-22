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
        r"|pesas|entren|rutina|ejercici|gimnasio|adelgazar|musculacion|musculation|seance"
        r"|entrainement|abnehmen|krafttraining|ernahrung|frauen|muskel|allenamento|palestra|treino"
        r"|academia|emagrecer|antrenman|kuwait|dubai|habesha|espanol|latino|deutsch"
        r"|francais|italiano|brasil|polska|turkiye"
        r"|bargain|drsquatch"
    ),
}


def is_harvestable_hashtag(tag: str, niche: str | None = None) -> bool:
    """Worth feeding into the discovery flywheel: long enough to mean
    something, not generic engagement bait, not just digits, not rejected by
    reviewers, and on-niche when `niche` has stems."""
    tag = tag.strip().lower()
    key = (niche or "").strip().lower()
    stems = HARVEST_STEMS.get(key)
    # "gluten" contains the "glute" stem but is food, not fitness.
    on_niche = not stems or any(stem in tag.replace("gluten", "") for stem in stems)
    excluded = HARVEST_EXCLUDED.get(key)
    if excluded is not None and excluded.search(tag):
        return False
    if key == "fitness" and not tag.isascii():
        return False
    return (
        len(tag) >= 3
        and not tag.isdigit()
        and tag not in GENERIC_HASHTAGS
        and tag not in REJECTED_HASHTAGS.get(key, ())
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
    r"(?i)(?:\.(?:uk|de|nl|fr|pl|es|it|au|ie|se|dk|no|cz|be|at|ch|br|mx"
    r"|nz|za|in|pt|fi|ro|hu|sk|hr|bg|gr|tr|ru|ua|ar|cl|pe|sg|my|ph|id|th|vn|ae|sa|il|ng|ke|pk|jp|kr|cn)"
    r"|@(?:gmx\.net|laposte\.net|naver\.com|daum\.net|qq\.com|163\.com|126\.com|interia\.eu))$"
)
_BLOCKED_EMAIL_DOMAINS = re.compile(r"(?i)@[^@\s]*grail-talent\b")

# Language gate: only English creators are allowed.
_STRIP_NOISE = re.compile(r"https?://\S+|\S+@\S+|#\S+|@\S+")
_NON_LATIN = re.compile(
    r"[\u0400-\u052F\u0600-\u06FF\u0590-\u05FF\u0900-\u097F\u0E00-\u0E7F"
    r"\u1100-\u11FF\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF\u1200-\u137F\u0370-\u03FF]"
)
_NON_EN_DIACRITICS = re.compile(
    r"[ßäöüÄÖÜñÑ¿¡çÇãõÃÕłŁąęśźżćńĄĘŚŹŻĆŃşğıŞĞİăîșțĂÎȘȚěščřžůĚŠČŘŽŮ]"
)
_NON_EN_WORDS = re.compile(
    r"(?i)(?<![a-z])(?:"
    # Spanish / Portuguese
    r"para|por|una|que|hola|aqu[ií]|tambi[eé]n|pero|estoy|siempre|ahora|vida|cuerpo"
    r"|entrenamiento|entrena|ejercicios?|rutinas?|pesas|gimnasio|adelgazar|recetas?"
    r"|nosotros|ayudo|amar|disfrutar|trabajo|poco|estilo"
    r"|n[aã]o|voc[eê]s?|muito|tudo|seu|sua|meu|minha|treino|academia|emagrecer|sa[uú]de"
    # German
    r"|und|ich|nicht|f[uü]r|auf|das|ein|eine|einen|einem|mein|meine|dein|deine"
    r"|auch|oder|aber|wir|ihr|hier|noch|schon|immer|sportlich|abnehmen|krafttraining|ern[aä]hrung|unterwegs"
    # French
    r"|pour|avec|dans|vous|nous|mais|tout|tr[eè]s|bien|[eê]tre|faire|chez|ici|suis"
    r"|s[eé]ance|entra[iî]nement|musculation|ann[eé]e|nouvelle|rejoins"
    # Italian
    r"|che|sono|della|delle|degli|gli|anche|tutto|mio|mia|pi[uù]|oggi"
    r"|allenamento|palestra|femminile|scopri|miei|ricette|motivazione"
    # Dutch
    r"|een|voor|niet|ook|maar|zijn|naar|mijn|jouw|geen|meer|weer|sporten|afvallen"
    # Polish
    r"|jest|nie|si[eę]|dla|jak|tylko|moje|tw[oó]j|trening|si[lł]ownia|odchudzanie"
    # Turkish
    r"|i[cç]in|[cç]ok|daha|spor|antrenman"
    # Indonesian / Malay
    r"|yang|untuk|dengan|dari|tidak|bisa|akan|juga|saya|kamu|karena|akhirnya|wajib|lengkap|sampai|habis|makin|banyak"
    r")(?![a-z])"
)
_EN_WORDS = re.compile(
    r"(?i)(?<![a-z])(?:"
    r"the|and|for|with|this|that|your|you|my|our|from|have|just|not|but|are|was|were"
    r"|all|out|about|how|what|when|will|can|get|here|more|like|into|they|them|their"
    r"|been|only|also|want|make|know|need|don't|i'm|it's|you're|helping|link|below"
    r")(?![a-z])"
)


def is_non_english(bio: str, captions: Sequence[str]) -> bool:
    """Whether the creator's bio or recent captions are predominantly non-English."""
    bio_clean = _STRIP_NOISE.sub(" ", bio or "")
    cap_cleans = [_STRIP_NOISE.sub(" ", c or "") for c in captions]
    combined = " ".join([bio_clean, *cap_cleans])

    if len(_NON_LATIN.findall(combined)) >= 3:
        return True

    bio_non_en = len(_NON_EN_WORDS.findall(bio_clean))
    bio_en = len(_EN_WORDS.findall(bio_clean))
    bio_dia = len(_NON_EN_DIACRITICS.findall(bio_clean))
    if (
        (bio_non_en >= 2 and bio_non_en > bio_en)
        or (bio_non_en >= 1 and bio_dia >= 1 and bio_en == 0)
        or (bio_dia >= 3 and bio_en == 0)
    ):
        return True

    if not cap_cleans:
        return False

    cap_non_en_count = 0
    total_non_en = bio_non_en
    total_en = bio_en
    total_dia = bio_dia
    for text in cap_cleans:
        ne = len(_NON_EN_WORDS.findall(text))
        en = len(_EN_WORDS.findall(text))
        dia = len(_NON_EN_DIACRITICS.findall(text))
        total_non_en += ne
        total_en += en
        total_dia += dia
        if (ne >= 2 and ne > en) or (ne >= 1 and dia >= 1 and en == 0):
            cap_non_en_count += 1

    return (
        (cap_non_en_count >= 2 and total_non_en >= total_en * 0.5)
        or (total_non_en >= 4 and total_non_en > total_en)
        or (total_dia >= 4 and total_non_en >= 2 and total_non_en >= total_en * 0.5)
    )


def rejected_group(niche: str, bio: str, captions: Sequence[str], email: str) -> str | None:
    """The group reviewers reject this creator with, if any: a blocked email
    domain, a non-English creator, a sport/food/family/non-gym word in the bio,
    a sport/food/non-gym word in two recent captions, or a country email domain."""
    if email and _BLOCKED_EMAIL_DOMAINS.search(email.strip()):
        return "blocked email domain"
    if is_non_english(bio, captions):
        return "non-english creator"
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
