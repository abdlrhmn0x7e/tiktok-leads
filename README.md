# tiktok-leads

Local TikTok lead scraper using `uv`, SQLite, and a replaceable TikTok source adapter.

## Setup

```bash
uv sync
uv run playwright install chromium
```

Create a `.env` file if you want notifications:

```env
NOTIFICATION_CHANNEL=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# or:
# NOTIFICATION_CHANNEL=telegram
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...
```

Recommended scrape config:

```env
SCRAPE_MODE=polite                 # polite | balanced | aggressive
SCRAPE_PROFILE_DELAY_SECONDS=8     # base wait between creators
SCRAPE_PROFILE_JITTER_SECONDS=4    # random extra wait between creators
SCRAPE_BURST_SIZE=1                # work items per daemon cycle
SCRAPE_REST_SECONDS=7200           # 2h rest between daemon cycles
SCRAPE_REST_JITTER_SECONDS=1800    # up to +30m extra rest
SCRAPE_RECENT_VIDEO_COUNT=6        # videos checked only for email-bearing profiles
DISCOVERY_SEARCH=false             # TikTok search is useful but often block-prone
```

Optional TikTok session config:

```env
TIKTOK_MS_TOKEN=...
TIKTOK_BROWSER_HEADLESS=false
TIKTOK_BROWSER=webkit
TIKTOK_STARTING_URL=https://www.tiktok.com
TIKTOK_SESSION_TIMEOUT_MS=90000
TIKTOK_SESSION_RETRIES=2
TIKTOK_SUPPRESS_LIBRARY_ERRORS=true

# Optional proxy. For Webshare rotating endpoint this is usually:
PROXY_SERVER=http://p.webshare.io:80
PROXY_USERNAME=your_webshare_username
PROXY_PASSWORD=your_webshare_password
```

If TikTok returns `EmptyResponseException`, try `TIKTOK_BROWSER_HEADLESS=false` first. If it still fails, try `TIKTOK_BROWSER=webkit`, then add a fresh `TIKTOK_MS_TOKEN` from your browser session.

## Usage

Initialize the database:

```bash
uv run tiktok-leads --niche fitness --init-db
```

Send a test notification:

```bash
uv run tiktok-leads --niche fitness --test-notification
```

Inspect known profiles:

```bash
uv run tiktok-leads --niche fitness --handle somecreator --handle anothercreator
```

Crawl a hashtag:

```bash
uv run tiktok-leads --niche mom --hashtag momlife --limit 30
```

Use a configured niche preset:

```bash
uv run tiktok-leads --niche fitness --limit 30
```

By default, the scraper requires at least 10,000 followers and 10,000 average views across recent videos.

## Run once and forget (daemon mode)

The commands above do a single pass and exit. Daemon mode runs forever instead:
it scrapes a small slice of hashtags each cycle, rests, and repeats — and when
TikTok starts blocking, it backs off for hours and **resumes** rather than
crashing.

```bash
uv run tiktok-leads --daemon --niche fitness
uv run tiktok-leads --daemon --niche fitness,mom   # rotate across several niches
```

To keep it alive across crashes, reboots aside, use the bundled relauncher
(it restarts the daemon if the process ever exits):

```bash
./run.sh fitness,mom
```

> **Reality check:** from a single home IP with no proxies, you *will* still get
> throttled sometimes — that's unavoidable without paying for a proxy pool or a
> managed API. Daemon mode doesn't make you invisible; it makes blocks survivable.
> It scrapes slowly and politely (substituting patience for IP rotation), rides
> out blocks, and never loses progress (dedup of evaluated handles lives in
> SQLite, so a restart picks up where the data left off).

### How it paces itself

Each cycle pulls `SCRAPE_BURST_SIZE` work items, crawls them using the
`SCRAPE_PROFILE_*` pacing, then sleeps `SCRAPE_REST_SECONDS` plus jitter before
the next slice. Over several cycles it sweeps every hashtag in the niche; the
SQLite handle-dedup stops it re-checking the same creators.

On a sustained block it sleeps with exponential backoff, resetting once a cycle
completes cleanly.

Tip: to scrape more politely, raise `SCRAPE_PROFILE_DELAY_SECONDS`, raise
`SCRAPE_REST_SECONDS`, or lower `SCRAPE_BURST_SIZE`. Slow and steady gets
blocked far less than fast bursts.

## Keeping lead volume up as the dataset grows

A fixed hashtag list dries up: once you've evaluated the creators it surfaces,
each pass finds fewer fresh ones. The daemon counters this with three discovery
sources that feed into the same work queue:

1. **Harvested hashtags (flywheel).** Every scraped video's hashtags are saved
   to `discovered_hashtags`. Tags seen often enough get crawled on later sweeps,
   so the hashtag pool grows itself — more scraping surfaces more hashtags,
   which surface more creators. (No extra requests: harvested from videos
   already fetched for view counts.)
2. **User search.** If `DISCOVERY_SEARCH=true`, `api.search.users` is queried
   for niche phrases (see `NICHE_SEARCH_TERMS` in `niches.py`) — these surface
   creators the hashtag feed never shows, but can be more block-prone.
3. **Stale re-checks.** Creators add emails to their bios over time. Profiles
   that qualified on followers/views but had no email (`skip_reason = "no_email"`)
   are re-checked once they're older than `RECHECK_AFTER_DAYS`, turning your
   growing dataset into a renewable lead source. (Only profiles scraped *after*
   this feature shipped carry a skip reason, so this pool builds up over time.)

```env
DISCOVERY_USE_HARVESTED_HASHTAGS=true
DISCOVERY_HARVESTED_HASHTAGS_PER_NICHE=15    # cap harvested tags added per sweep
DISCOVERY_HARVESTED_MIN_MENTIONS=2           # only crawl tags seen at least this often
DISCOVERY_HARVESTED_RECRAWL_AFTER_DAYS=7     # don't recrawl a harvested tag sooner
DISCOVERY_SEARCH=false
DISCOVERY_SEARCH_RESULTS_PER_TERM=20         # users requested per search phrase when enabled
RECHECK_ENABLED=true
RECHECK_AFTER_DAYS=21                         # a profile is "stale" once unseen this long
RECHECK_HANDLES_PER_SWEEP=10                  # stale handles to re-check each sweep
```

> `api.search.users` needs an `ms_token` that has performed a search before —
> set a fresh `TIKTOK_MS_TOKEN` from a logged-in browser session for best
> results.
