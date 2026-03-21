from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import CdSettings

LOGGER = logging.getLogger(__name__)


def post_webhook(url: str, payload: dict) -> None:  # type: ignore[type-arg]
    """POST *payload* as JSON to *url*.  Errors are logged and swallowed."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode(errors="replace")
        except Exception:  # noqa: BLE001
            pass
        LOGGER.warning("cd.notify_failed url=%s status=%s body=%s", url, exc.code, body)
    except (urllib.error.URLError, OSError) as exc:
        LOGGER.warning("cd.notify_failed url=%s error=%s", url, exc)


def _slack_payload(text: str) -> dict:  # type: ignore[type-arg]
    return {"text": text}


def _discord_payload(text: str) -> dict:  # type: ignore[type-arg]
    return {"content": text}


def notify(settings: CdSettings, message: str) -> None:
    """Send *message* to Slack and/or Discord webhooks simultaneously.

    If dry_run is enabled the message is only logged, not posted.
    """
    slack_url = settings.notify_slack_webhook_url
    discord_url = settings.notify_discord_webhook_url

    if not slack_url and not discord_url:
        return

    if settings.dry_run:
        LOGGER.info("cd.notify_dry_run message=%r", message)
        return

    threads: list[threading.Thread] = []

    if slack_url:
        t = threading.Thread(
            target=post_webhook,
            args=(slack_url, _slack_payload(message)),
            daemon=True,
        )
        threads.append(t)

    if discord_url:
        t = threading.Thread(
            target=post_webhook,
            args=(discord_url, _discord_payload(message)),
            daemon=True,
        )
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
