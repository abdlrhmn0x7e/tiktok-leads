from __future__ import annotations

import requests

from tiktok_leads.models import Lead
from tiktok_leads.settings import Settings


class Notifier:
    def send(self, lead: Lead) -> None:
        raise NotImplementedError

    def send_text(self, text: str) -> None:
        raise NotImplementedError


class NoopNotifier(Notifier):
    def send(self, lead: Lead) -> None:
        return None

    def send_text(self, text: str) -> None:
        return None


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, lead: Lead) -> None:
        self._post(format_discord_payload(lead))

    def send_text(self, text: str) -> None:
        self._post({"content": text})

    def _post(self, payload: dict) -> None:
        response = requests.post(self.webhook_url, json=payload, timeout=20)
        if not response.ok:
            raise RuntimeError(f"Discord webhook failed: {response.status_code} {response.text}")


class TelegramNotifier(Notifier):
    def __init__(self, *, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, lead: Lead) -> None:
        self.send_text(format_lead_message(lead))

    def send_text(self, text: str) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text},
            timeout=20,
        )
        response.raise_for_status()


class ConvexNotifier(Notifier):
    """Pushes each new lead into the leads-hub Convex backend, which is the
    source of truth for reviewing/outreach. The endpoint dedupes by email, so
    the runner's mark-notified/retry loop is safe to reuse."""

    def __init__(self, *, ingest_url: str, api_key: str) -> None:
        self.ingest_url = ingest_url.rstrip("/")
        self.api_key = api_key

    def send(self, lead: Lead) -> None:
        response = requests.post(
            f"{self.ingest_url}/ingest/leads",
            json={
                "handle": lead.handle,
                "profile_url": lead.profile_url,
                "email": lead.email,
                "niche": lead.niche,
                "followers_count": lead.followers_count,
                "average_views": lead.average_views,
                "source": lead.source,
                "scraped_at": lead.created_at.isoformat(),
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(f"Convex ingest failed: {response.status_code} {response.text}")

    def send_text(self, text: str) -> None:
        # Digests/status messages stay on chat channels; Convex only gets leads.
        return None


class MultiNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, lead: Lead) -> None:
        self._fan_out(lambda notifier: notifier.send(lead))

    def send_text(self, text: str) -> None:
        self._fan_out(lambda notifier: notifier.send_text(text))

    def _fan_out(self, action) -> None:
        errors: list[str] = []
        for notifier in self.notifiers:
            try:
                action(notifier)
            except Exception as exc:  # noqa: BLE001 - one channel must not kill the rest
                errors.append(f"{type(notifier).__name__}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))


def _build_channel_notifier(channel: str, settings: Settings) -> Notifier:
    if channel == "discord":
        if not settings.discord_webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL is required when NOTIFICATION_CHANNEL=discord")
        return DiscordNotifier(settings.discord_webhook_url)
    if channel == "telegram":
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when NOTIFICATION_CHANNEL=telegram"
            )
        return TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    if channel == "convex":
        if not settings.convex_site_url or not settings.convex_ingest_api_key:
            raise ValueError(
                "CONVEX_SITE_URL and CONVEX_INGEST_API_KEY are required when NOTIFICATION_CHANNEL=convex"
            )
        return ConvexNotifier(
            ingest_url=settings.convex_site_url,
            api_key=settings.convex_ingest_api_key,
        )
    if channel in ("", "none"):
        return NoopNotifier()
    raise ValueError(f"Unknown notification channel: {channel}")


def build_notifier(settings: Settings) -> Notifier:
    """NOTIFICATION_CHANNEL accepts a comma-separated list, e.g. "convex,telegram"."""
    channels = [c.strip().lower() for c in settings.notification_channel.split(",") if c.strip()]
    notifiers = [
        _build_channel_notifier(channel, settings) for channel in channels or ["none"]
    ]
    notifiers = [n for n in notifiers if not isinstance(n, NoopNotifier)]
    if not notifiers:
        return NoopNotifier()
    if len(notifiers) == 1:
        return notifiers[0]
    return MultiNotifier(notifiers)


def format_lead_message(lead: Lead) -> str:
    return "\n".join(
        [
            "New TikTok influencer found",
            "",
            f"Handle: `{format_handle(lead.handle)}`",
            f"Niche: `{lead.niche}`",
            f"Followers: `{lead.followers_count:,}`",
            f"Average views: `{lead.average_views:,}`",
            f"Email: `{lead.email}`",
            f"Profile: `{lead.profile_url}`",
        ]
    )


def format_discord_payload(lead: Lead) -> dict:
    return {
        "content": "\n".join(
            [
                "**New TikTok influencer found**",
                "",
                f"Niche: `{lead.niche}`",
                f"Followers: `{lead.followers_count:,}`",
                "",
                "Handle",
                "```text",
                format_handle(lead.handle),
                "```",
                "Average views",
                "```text",
                f"{lead.average_views:,}",
                "```",
                "Email",
                "```text",
                lead.email,
                "```",
                f"Profile: {lead.profile_url}",
            ]
        ),
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Open TikTok",
                        "url": lead.profile_url,
                    }
                ],
            }
        ],
    }


def format_handle(handle: str) -> str:
    return f"@{handle.removeprefix('@')}"
