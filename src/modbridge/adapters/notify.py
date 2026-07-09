"""Notification sinks. Discord webhook now; the protocol allows more later."""

from __future__ import annotations

import logging

import httpx

from modbridge.adapters.base import Notification

log = logging.getLogger(__name__)

_GREEN = 0x2ECC71
_RED = 0xE74C3C
_MAX_DESCRIPTION = 3900  # Discord embed limit is 4096


class DiscordNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, notification: Notification) -> None:
        body = notification.body
        if len(body) > _MAX_DESCRIPTION:
            body = body[:_MAX_DESCRIPTION] + "\n…(truncated)"
        embed = {
            "title": notification.title,
            "description": body,
            "color": _GREEN if notification.success else _RED,
            "fields": [
                {"name": k, "value": v[:1024], "inline": True}
                for k, v in notification.fields.items()
            ],
        }
        try:
            resp = httpx.post(self.webhook_url, json={"embeds": [embed]}, timeout=15.0)
            if resp.status_code >= 300:
                log.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            log.warning("Discord notification failed: %s", exc)


class LogNotifier:
    """Fallback sink: notifications land in the log file."""

    def send(self, notification: Notification) -> None:
        level = logging.INFO if notification.success else logging.ERROR
        log.log(level, "%s\n%s", notification.title, notification.body)
