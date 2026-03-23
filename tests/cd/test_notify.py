from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.cd.config import CdSettings
from src.cd.notify import notify, post_webhook


def _settings(**kwargs) -> CdSettings:  # type: ignore[no-untyped-def]
    defaults = dict(
        image="ghcr.io/org/image",
        image_tag="latest",
        container_name="codex-slack-master",
        compose_file="docker-compose.master.yml",
        compose_service="codex-slack-master",
        compose_binary="docker compose",
        env_file=None,
        state_file="/tmp/cd-test-state.json",
        poll_interval_seconds=60,
        health_check_delay_seconds=1,
        rollback_on_failure=True,
        dry_run=False,
        notify_slack_webhook_url=None,
        notify_discord_webhook_url=None,
    )
    defaults.update(kwargs)
    return CdSettings(**defaults)


# ---------------------------------------------------------------------------
# post_webhook
# ---------------------------------------------------------------------------


def test_post_webhook_sends_json() -> None:
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_ctx) as mock_open, \
         patch("urllib.request.Request") as mock_req:
        post_webhook("https://hooks.slack.com/test", {"text": "hello"})
    mock_req.assert_called_once()
    mock_open.assert_called_once()


def test_post_webhook_swallows_network_error() -> None:
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
        # Should not raise.
        post_webhook("https://hooks.slack.com/test", {"text": "hello"})


# ---------------------------------------------------------------------------
# notify — no-op cases
# ---------------------------------------------------------------------------


def test_notify_does_nothing_when_no_urls_configured() -> None:
    with patch("src.cd.notify.post_webhook") as mock_post:
        notify(_settings(), "some message")
    mock_post.assert_not_called()


def test_notify_dry_run_skips_post() -> None:
    with patch("src.cd.notify.post_webhook") as mock_post:
        notify(
            _settings(
                dry_run=True,
                notify_slack_webhook_url="https://hooks.slack.com/x",
                notify_discord_webhook_url="https://discord.com/api/webhooks/x",
            ),
            "dry run message",
        )
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# notify — both webhooks
# ---------------------------------------------------------------------------


def test_notify_posts_to_slack_and_discord_simultaneously() -> None:
    slack_url = "https://hooks.slack.com/x"
    discord_url = "https://discord.com/api/webhooks/x"
    calls: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    def fake_post(url: str, payload: dict) -> None:  # type: ignore[type-arg]
        calls.append((url, payload))

    with patch("src.cd.notify.post_webhook", side_effect=fake_post):
        notify(
            _settings(
                notify_slack_webhook_url=slack_url,
                notify_discord_webhook_url=discord_url,
            ),
            "deploy done",
        )

    urls = {c[0] for c in calls}
    assert slack_url in urls
    assert discord_url in urls

    slack_payload = next(c[1] for c in calls if c[0] == slack_url)
    discord_payload = next(c[1] for c in calls if c[0] == discord_url)
    assert slack_payload == {"text": "deploy done"}
    assert discord_payload == {"content": "deploy done"}


def test_notify_slack_only() -> None:
    slack_url = "https://hooks.slack.com/x"
    with patch("src.cd.notify.post_webhook") as mock_post:
        notify(_settings(notify_slack_webhook_url=slack_url), "msg")
    mock_post.assert_called_once()
    assert mock_post.call_args[0][0] == slack_url
    assert mock_post.call_args[0][1] == {"text": "msg"}


def test_notify_discord_only() -> None:
    discord_url = "https://discord.com/api/webhooks/x"
    with patch("src.cd.notify.post_webhook") as mock_post:
        notify(_settings(notify_discord_webhook_url=discord_url), "msg")
    mock_post.assert_called_once()
    assert mock_post.call_args[0][0] == discord_url
    assert mock_post.call_args[0][1] == {"content": "msg"}
