"""Unit tests for src/master/notify.py — agent message notification module."""
from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from src.master.notify import (
    NotificationContent,
    _discord_payload,
    _render_body,
    _telegram_payload,
    build_topic_url,
    notify_reply,
    post_webhook,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(
    *,
    master_public_url: str = "",
    notify_discord_webhook_url: str = "",
    notify_telegram_bot_token: str = "",
    notify_telegram_chat_id: str = "",
    notify_preview_chars: int = 200,
    dry_run: bool = False,
) -> Any:
    """Return a minimal MasterSettings-compatible object for notification tests."""

    @dataclass(frozen=True)
    class _Settings:
        master_public_url: str
        notify_discord_webhook_url: str
        notify_telegram_bot_token: str
        notify_telegram_chat_id: str
        notify_preview_chars: int
        dry_run: bool

    return _Settings(
        master_public_url=master_public_url,
        notify_discord_webhook_url=notify_discord_webhook_url,
        notify_telegram_bot_token=notify_telegram_bot_token,
        notify_telegram_chat_id=notify_telegram_chat_id,
        notify_preview_chars=notify_preview_chars,
        dry_run=dry_run,
    )


def _make_db(tmp_path, *, workspace_name: str = "My Workspace", topic_subject: str = "Fix bug #42") -> str:
    """Create a minimal SQLite database pre-seeded with one workspace and one topic."""
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    now = "2026-01-01T00:00:00Z"
    conn.executescript(f"""
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            repo_url TEXT NOT NULL,
            container_name TEXT,
            created_at TEXT NOT NULL,
            archived_at TEXT,
            last_refreshed_at TEXT,
            last_message_at TEXT,
            last_dispatched_at TEXT,
            last_responded_at TEXT
        );
        CREATE TABLE topics (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            worktree_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE config (
            scope_type TEXT NOT NULL,
            scope_id   TEXT NOT NULL DEFAULT '',
            key        TEXT NOT NULL,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, key)
        );
        INSERT INTO workspaces (id, name, repo_url, created_at)
            VALUES ('ws1', '{workspace_name}', 'https://github.com/x/y', '{now}');
        INSERT INTO topics (id, workspace_id, subject, branch_name, worktree_path, created_at)
            VALUES ('t1', 'ws1', '{topic_subject}', 'fix-bug-42', '/tmp/wt', '{now}');
    """)
    conn.commit()
    conn.close()
    return db


def _set_config(db: str, scope_type: str, scope_id: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT OR REPLACE INTO config (scope_type, scope_id, key, value, updated_at) VALUES (?, ?, ?, ?, ?)",
        (scope_type, scope_id, key, value, now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# build_topic_url
# ---------------------------------------------------------------------------

class TestBuildTopicUrl:
    def test_normal_url(self):
        result = build_topic_url("https://codex.example.com", "ws1", "t1")
        assert result == "https://codex.example.com/workspaces/ws1/topics/t1"

    def test_trailing_slash_stripped(self):
        result = build_topic_url("https://codex.example.com/", "ws1", "t1")
        assert result == "https://codex.example.com/workspaces/ws1/topics/t1"

    def test_multiple_trailing_slashes_stripped(self):
        result = build_topic_url("https://codex.example.com///", "ws1", "t1")
        assert result == "https://codex.example.com/workspaces/ws1/topics/t1"

    def test_none_public_url_returns_none(self):
        assert build_topic_url(None, "ws1", "t1") is None

    def test_empty_string_public_url_returns_none(self):
        assert build_topic_url("", "ws1", "t1") is None

    def test_whitespace_only_public_url_returns_none(self):
        assert build_topic_url("   ", "ws1", "t1") is None


# ---------------------------------------------------------------------------
# _render_body
# ---------------------------------------------------------------------------

class TestRenderBody:
    def _content(self, **kwargs) -> NotificationContent:
        defaults = dict(
            workspace_name="My Workspace",
            topic_subject="Fix bug #42",
            topic_url="https://codex.example.com/workspaces/ws1/topics/t1",
            preview="The agent fixed the bug.",
        )
        return NotificationContent(**{**defaults, **kwargs})

    def test_full_body_contains_all_parts(self):
        body = _render_body(self._content())
        assert "My Workspace" in body
        assert "Fix bug #42" in body
        assert "The agent fixed the bug." in body
        assert "https://codex.example.com/workspaces/ws1/topics/t1" in body

    def test_no_url_omits_url_line(self):
        body = _render_body(self._content(topic_url=None))
        assert "http" not in body
        assert "My Workspace" in body

    def test_empty_preview_omits_preview_line(self):
        body = _render_body(self._content(preview=""))
        assert "The agent" not in body
        # workspace and subject still present
        assert "My Workspace" in body
        assert "Fix bug #42" in body

    def test_no_url_no_preview(self):
        body = _render_body(self._content(topic_url=None, preview=""))
        assert "My Workspace" in body
        assert "Fix bug #42" in body
        assert "http" not in body


# ---------------------------------------------------------------------------
# _discord_payload
# ---------------------------------------------------------------------------

class TestDiscordPayload:
    def test_has_content_key(self):
        content = NotificationContent(
            workspace_name="WS",
            topic_subject="Subject",
            topic_url="https://x.com",
            preview="preview text",
        )
        payload = _discord_payload(content)
        assert "content" in payload
        assert isinstance(payload["content"], str)

    def test_content_includes_workspace_and_subject(self):
        content = NotificationContent(
            workspace_name="My WS",
            topic_subject="The subject",
            topic_url="https://x.com",
            preview="short preview",
        )
        payload = _discord_payload(content)
        assert "My WS" in payload["content"]
        assert "The subject" in payload["content"]


# ---------------------------------------------------------------------------
# _telegram_payload
# ---------------------------------------------------------------------------

class TestTelegramPayload:
    def test_has_chat_id_and_text(self):
        content = NotificationContent(
            workspace_name="WS",
            topic_subject="Subject",
            topic_url="https://x.com",
            preview="preview",
        )
        payload = _telegram_payload(content, chat_id="@mychannel")
        assert payload["chat_id"] == "@mychannel"
        assert "text" in payload
        assert "WS" in payload["text"]

    def test_disable_web_page_preview(self):
        content = NotificationContent(
            workspace_name="WS",
            topic_subject="Subject",
            topic_url=None,
            preview="",
        )
        payload = _telegram_payload(content, chat_id="12345")
        # design specifies disable_web_page_preview=true
        assert payload.get("disable_web_page_preview") is True


# ---------------------------------------------------------------------------
# post_webhook
# ---------------------------------------------------------------------------

class TestPostWebhook:
    def _make_response(self, status: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.status = status
        return resp

    def test_posts_json_body(self):
        payload = {"content": "hello"}
        with patch("src.master.notify.request.urlopen") as mock_open:
            mock_open.return_value = self._make_response()
            post_webhook("https://example.com/hook", payload)
        mock_open.assert_called_once()
        req = mock_open.call_args.args[0]
        body = json.loads(req.data)
        assert body == payload

    def test_http_500_does_not_raise(self):
        exc = urllib.error.HTTPError(
            url="https://example.com/hook",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=BytesIO(b"server error"),
        )
        with patch("src.master.notify.request.urlopen", side_effect=exc):
            # must not raise
            post_webhook("https://example.com/hook", {"content": "hello"})

    def test_http_500_is_logged(self, caplog):
        import logging

        exc = urllib.error.HTTPError(
            url="https://example.com/hook",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=BytesIO(b"server error"),
        )
        with caplog.at_level(logging.WARNING, logger="src.master.notify"):
            with patch("src.master.notify.request.urlopen", side_effect=exc):
                post_webhook("https://example.com/hook", {"content": "hello"})
        assert any("500" in r.message or "failed" in r.message.lower() for r in caplog.records)

    def test_url_error_does_not_raise(self):
        exc = urllib.error.URLError("connection refused")
        with patch("src.master.notify.request.urlopen", side_effect=exc):
            post_webhook("https://example.com/hook", {"content": "hello"})

    def test_dry_run_skips_http_call(self, caplog):
        import logging

        with caplog.at_level(logging.DEBUG, logger="src.master.notify"):
            with patch("src.master.notify.request.urlopen") as mock_open:
                post_webhook("https://example.com/hook", {"content": "hello"}, dry_run=True)
                mock_open.assert_not_called()

    def test_dry_run_emits_log_line(self, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="src.master.notify"):
            with patch("src.master.notify.request.urlopen"):
                post_webhook("https://example.com/hook", {"content": "test"}, dry_run=True)
        assert any("dry_run" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# notify_reply — main entry point
# ---------------------------------------------------------------------------

class TestNotifyReply:
    """Tests for notify_reply using a real in-memory-style SQLite db on disk."""

    def _run_notify_and_join(self, tmp_path, settings, payload=None, topic_id="t1"):
        """Call notify_reply and wait for background threads to complete.

        Threads started by notify_reply are daemons and unjoinable externally,
        so we patch threading.Thread to track and join them manually.
        """
        if payload is None:
            payload = {"last_response": "The agent replied."}
        db = _make_db(tmp_path)
        return db

    # ── TC-01: no channels configured — no-op ─────────────────────────────

    def test_no_channels_configured_no_post(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings()  # all notify fields empty
        threads_started: list[threading.Thread] = []
        original_thread = threading.Thread

        def capturing_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_started.append(t)
            return t

        with patch("src.master.notify.threading.Thread", side_effect=capturing_thread):
            with patch("src.master.notify.post_webhook") as mock_post:
                notify_reply(
                    db_path=db,
                    settings=settings,
                    topic_id="t1",
                    payload={"last_response": "hello"},
                )
        # No threads should have been spawned; no POST should be called
        assert len(threads_started) == 0
        mock_post.assert_not_called()

    # ── TC-02: Discord-only configured — one POST with correct payload ─────

    def test_discord_only_one_post_correct_payload(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "Agent is done."},
            )

        # Allow any threads to complete
        assert len(posted) == 1
        url, payload = posted[0]
        assert url == "https://discord.com/api/webhooks/123/abc"
        assert "content" in payload
        assert "My Workspace" in payload["content"]
        assert "Fix bug #42" in payload["content"]

    # ── TC-03: Telegram-only configured — one POST to correct URL ──────────

    def test_telegram_only_one_post_correct_url_and_body(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_telegram_bot_token="bot123:TOKEN",
            notify_telegram_chat_id="@testchannel",
        )
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "Done!"},
            )

        assert len(posted) == 1
        url, payload = posted[0]
        assert "api.telegram.org" in url
        assert "bot123:TOKEN" in url
        assert "sendMessage" in url
        assert payload["chat_id"] == "@testchannel"
        assert "text" in payload

    # ── TC-04: Both configured — two POSTs ────────────────────────────────

    def test_both_configured_two_posts(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/999/xyz",
            notify_telegram_bot_token="bot456:TOKEN",
            notify_telegram_chat_id="12345678",
        )
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "Agent replied."},
            )

        assert len(posted) == 2
        urls = {url for url, _ in posted}
        discord_urls = {u for u in urls if "discord.com" in u}
        telegram_urls = {u for u in urls if "telegram.org" in u}
        assert len(discord_urls) == 1
        assert len(telegram_urls) == 1

    # ── TC-05: Workspace config overrides global Discord webhook ──────────

    def test_workspace_discord_webhook_overrides_global(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/GLOBAL/hook",
        )
        # Set workspace-level override
        _set_config(db, "workspace", "ws1", "NOTIFY_DISCORD_WEBHOOK_URL",
                    "https://discord.com/api/webhooks/WORKSPACE/hook")

        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "done"},
            )

        assert len(posted) == 1
        url, _ = posted[0]
        assert "WORKSPACE" in url
        assert "GLOBAL" not in url

    # ── TC-06: NOTIFY_DISABLED=true skips all channels ────────────────────

    def test_notify_disabled_skips_all_channels(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            notify_telegram_bot_token="bot123:TOKEN",
            notify_telegram_chat_id="@chan",
        )
        _set_config(db, "workspace", "ws1", "NOTIFY_DISABLED", "true")

        with patch("src.master.notify.post_webhook") as mock_post:
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "reply"},
            )

        mock_post.assert_not_called()

    def test_notify_disabled_uppercase_true_skips(self, tmp_path):
        """NOTIFY_DISABLED=TRUE (all-caps) should also be treated as truthy."""
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        _set_config(db, "workspace", "ws1", "NOTIFY_DISABLED", "TRUE")

        with patch("src.master.notify.post_webhook") as mock_post:
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "reply"},
            )

        mock_post.assert_not_called()

    # ── TC-07: MASTER_PUBLIC_URL unset — no URL in notification body ───────

    def test_no_public_url_omits_url_from_body(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            master_public_url="",  # unset
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "done"},
            )

        assert len(posted) == 1
        _, payload = posted[0]
        body_text = payload.get("content", "")
        assert "http" not in body_text

    # ── TC-08: NOTIFY_PREVIEW_CHARS=0 — no preview in body ───────────────

    def test_preview_chars_zero_omits_preview(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            notify_preview_chars=0,
        )
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "This should not appear in the notification."},
            )

        assert len(posted) == 1
        _, payload = posted[0]
        body_text = payload.get("content", "")
        assert "This should not appear" not in body_text

    # ── TC-09: Long reply truncated to NOTIFY_PREVIEW_CHARS with ellipsis ─

    def test_long_reply_truncated_with_ellipsis(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            notify_preview_chars=10,
        )
        long_reply = "A" * 200
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": long_reply},
            )

        assert len(posted) == 1
        _, payload = posted[0]
        body_text = payload.get("content", "")
        # The full long reply must not appear
        assert long_reply not in body_text
        # An ellipsis must be appended to signal truncation
        assert "…" in body_text or "..." in body_text

    def test_reply_at_exact_limit_not_truncated(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            notify_preview_chars=5,
        )
        exact_reply = "Hello"  # exactly 5 chars
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": exact_reply},
            )

        assert len(posted) == 1
        _, payload = posted[0]
        body_text = payload.get("content", "")
        assert "Hello" in body_text
        # No ellipsis when reply fits exactly
        assert "…" not in body_text

    # ── TC-10: Provider returns 500 — logged, does not raise, sibling delivered

    def test_discord_500_sibling_telegram_still_delivered(self, tmp_path):
        """When Discord urlopen returns 500, post_webhook swallows it and Telegram is still POSTed.

        Both channels are dispatched on separate daemon threads.  We patch
        request.urlopen so the real post_webhook runs (and logs) the error, then
        we also track every urlopen call to confirm the Telegram URL is reached.
        """
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/FAIL/hook",
            notify_telegram_bot_token="bot123:TOKEN",
            notify_telegram_chat_id="@chan",
        )

        called_urls: list[str] = []
        discord_exc = urllib.error.HTTPError(
            url="https://discord.com/api/webhooks/FAIL/hook",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=BytesIO(b"err"),
        )

        def fake_urlopen(req, timeout=None):
            called_urls.append(req.full_url)
            if "discord" in req.full_url:
                raise discord_exc
            # Return a context-manager-compatible mock for Telegram
            resp = MagicMock()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        # Threads are daemon and unjoinable externally; capture threads to join them.
        spawned: list[threading.Thread] = []
        original_thread_start = threading.Thread.start

        def capturing_start(self_thread):
            spawned.append(self_thread)
            original_thread_start(self_thread)

        with patch("src.master.notify.request.urlopen", side_effect=fake_urlopen):
            with patch.object(threading.Thread, "start", capturing_start):
                notify_reply(
                    db_path=db,
                    settings=settings,
                    topic_id="t1",
                    payload={"last_response": "done"},
                )
            # Wait for all daemon threads to complete
            for t in spawned:
                t.join(timeout=5)

        # Telegram URL should have been called despite Discord's 500
        assert any("telegram" in u for u in called_urls)

    # ── TC-11: dry_run=True — no HTTP call, log line emitted ──────────────

    def test_dry_run_no_http_call(self, tmp_path, caplog):
        import logging  # noqa: PLC0415

        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            dry_run=True,
        )

        spawned: list[threading.Thread] = []
        original_thread_start = threading.Thread.start

        def capturing_start(self_thread):
            spawned.append(self_thread)
            original_thread_start(self_thread)

        with caplog.at_level(logging.INFO, logger="src.master.notify"):
            with patch("src.master.notify.request.urlopen") as mock_open:
                with patch.object(threading.Thread, "start", capturing_start):
                    notify_reply(
                        db_path=db,
                        settings=settings,
                        topic_id="t1",
                        payload={"last_response": "reply"},
                    )
                for t in spawned:
                    t.join(timeout=5)
                mock_open.assert_not_called()

        assert any("dry_run" in r.message for r in caplog.records)

    # ── TC-12: Unknown / deleted topic — log and return, no crash ─────────

    def test_deleted_topic_logs_and_returns(self, tmp_path, caplog):
        import logging

        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )

        with caplog.at_level(logging.WARNING, logger="src.master.notify"):
            with patch("src.master.notify.post_webhook") as mock_post:
                notify_reply(
                    db_path=db,
                    settings=settings,
                    topic_id="nonexistent-topic",
                    payload={"last_response": "reply"},
                )
                mock_post.assert_not_called()

    # ── TC-13: Telegram missing chat_id — Telegram skipped, no crash ──────

    def test_telegram_token_without_chat_id_skips_telegram(self, tmp_path):
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            notify_telegram_bot_token="bot123:TOKEN",
            notify_telegram_chat_id="",  # missing
        )
        posted: list[str] = []

        def fake_post(url, payload, dry_run=False):
            posted.append(url)

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "done"},
            )

        # Only Discord should have been called; no Telegram
        assert len(posted) == 1
        assert "discord" in posted[0]

    # ── Workspace config overrides: NOTIFY_PREVIEW_CHARS ──────────────────

    def test_workspace_preview_chars_override(self, tmp_path):
        """Workspace NOTIFY_PREVIEW_CHARS=5 overrides the global default of 200."""
        db = _make_db(tmp_path)
        settings = _make_settings(
            notify_discord_webhook_url="https://discord.com/api/webhooks/123/abc",
            notify_preview_chars=200,  # global default
        )
        _set_config(db, "workspace", "ws1", "NOTIFY_PREVIEW_CHARS", "5")

        long_reply = "B" * 100
        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": long_reply},
            )

        assert len(posted) == 1
        _, payload = posted[0]
        body_text = payload.get("content", "")
        assert long_reply not in body_text
        # Truncated at 5, ellipsis appended
        assert "…" in body_text or "..." in body_text

    # ── Workspace Telegram override ────────────────────────────────────────

    def test_workspace_telegram_config_used_when_global_absent(self, tmp_path):
        """Workspace-only Telegram config triggers Telegram delivery."""
        db = _make_db(tmp_path)
        settings = _make_settings()  # no global telegram config
        _set_config(db, "workspace", "ws1", "NOTIFY_TELEGRAM_BOT_TOKEN", "wsbot:TOKEN")
        _set_config(db, "workspace", "ws1", "NOTIFY_TELEGRAM_CHAT_ID", "@wschan")

        posted: list[tuple[str, dict]] = []

        def fake_post(url, payload, dry_run=False):
            posted.append((url, payload))

        with patch("src.master.notify.post_webhook", side_effect=fake_post):
            notify_reply(
                db_path=db,
                settings=settings,
                topic_id="t1",
                payload={"last_response": "workspace reply"},
            )

        assert len(posted) == 1
        url, payload = posted[0]
        assert "telegram.org" in url
        assert "wsbot:TOKEN" in url
        assert payload["chat_id"] == "@wschan"
