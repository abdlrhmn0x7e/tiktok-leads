from __future__ import annotations

import requests

from tiktok_leads.models import Lead
from tiktok_leads.settings import Settings


class Notifier:
    def send(self, lead: Lead) -> None:
        raise NotImplementedError


class NoopNotifier(Notifier):
    def send(self, lead: Lead) -> None:
        return None


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, lead: Lead) -> None:
        response = requests.post(
            self.webhook_url,
            json={"content": format_lead_message(lead)},
            timeout=20,
        )
        response.raise_for_status()


class TelegramNotifier(Notifier):
    def __init__(self, *, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, lead: Lead) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": format_lead_message(lead)},
            timeout=20,
        )
        response.raise_for_status()


def build_notifier(settings: Settings) -> Notifier:
    channel = settings.notification_channel.lower()
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
    return NoopNotifier()


def format_lead_message(lead: Lead) -> str:
    return "\n".join(
        [
            "New TikTok influencer found",
            "",
            f"Handle: @{lead.handle}",
            f"Niche: {lead.niche}",
            f"Followers: {lead.followers_count:,}",
            f"Average views: {lead.average_views:,}",
            f"Email: {lead.email}",
            f"Profile: {lead.profile_url}",
        ]
    )
