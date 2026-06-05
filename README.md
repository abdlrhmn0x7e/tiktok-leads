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

Optional TikTok session config:

```env
TIKTOK_MS_TOKEN=...
TIKTOK_BROWSER_HEADLESS=false
TIKTOK_BROWSER=webkit
TIKTOK_STARTING_URL=https://www.tiktok.com
TIKTOK_SESSION_TIMEOUT_MS=90000
TIKTOK_SESSION_RETRIES=2
TIKTOK_REQUEST_DELAY_SECONDS=2
TIKTOK_REQUEST_JITTER_SECONDS=1.5
TIKTOK_MAX_CONSECUTIVE_BLOCKED_PROFILES=5
TIKTOK_BLOCK_COOLDOWN_SECONDS=10
TIKTOK_MAX_BLOCK_COOLDOWNS_PER_HASHTAG=1
TIKTOK_RESTART_SESSION_ON_BLOCK=true
TIKTOK_RESTART_SESSION_BETWEEN_HASHTAGS=true
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
