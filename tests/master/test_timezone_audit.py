"""Timezone audit tests — issue #158.

Verifies the project-wide TZ policy:
  - Storage: every *_at column written as UTC ISO-8601 with Z suffix.
  - Backend: _parse_iso_utc always returns a UTC-aware datetime.
  - Non-UTC display timezone: stored values remain UTC regardless of
    the system.timezone setting (e.g. Asia/Shanghai = UTC+8).
  - Scheduler: cron expressions are interpreted in the configured
    display TZ, not UTC; the resulting last_fired_at is stored as UTC.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.main import app, _parse_iso_utc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _is_utc_iso(value: str | None) -> bool:
    """Return True iff value matches YYYY-MM-DDTHH:MM:SSZ."""
    if value is None:
        return True  # NULL is acceptable for optional columns
    return bool(_UTC_ISO_RE.match(value))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client_shanghai(tmp_path, monkeypatch):
    """TestClient pre-configured with system.timezone = Asia/Shanghai."""
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with TestClient(app) as c:
        r = c.patch("/api/config/system-settings", json={"timezone": "Asia/Shanghai"})
        assert r.status_code == 200
        yield c


def _make_workspace(client) -> dict:
    r = client.post("/api/workspaces", json={"name": "tz-test-ws", "repo_url": "https://github.com/x/y"})
    assert r.status_code == 201
    return r.json()


def _make_topic(client, ws_id: str) -> dict:
    r = client.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "tz topic", "repo_ref": "main"})
    assert r.status_code == 201
    return r.json()


def _make_app_state(db_path: str) -> MagicMock:
    state = MagicMock()
    state.event_queue = asyncio.Queue()
    state.event_loop = None  # set inside asyncio.run() by tests that need it
    state.db_path = db_path
    state.hub = MagicMock()
    state.hub.broadcast = AsyncMock()
    state.mqtt = MagicMock()
    state.settings = MagicMock()
    state.settings.dry_run = True
    return state


# ---------------------------------------------------------------------------
# 1. _parse_iso_utc always returns a UTC-aware datetime
# ---------------------------------------------------------------------------


def test_parse_iso_utc_with_z_suffix():
    dt = _parse_iso_utc("2024-06-01T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 12


def test_parse_iso_utc_with_plus00():
    dt = _parse_iso_utc("2024-06-01T12:00:00+00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc


def test_parse_iso_utc_naive_fallback_attaches_utc():
    # Defensive fallback: naive string gets UTC attached with a warning.
    dt = _parse_iso_utc("2024-06-01T12:00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_iso_utc_empty_returns_none():
    assert _parse_iso_utc(None) is None
    assert _parse_iso_utc("") is None


def test_parse_iso_utc_garbage_returns_none():
    assert _parse_iso_utc("not-a-date") is None


# ---------------------------------------------------------------------------
# 2. Storage columns are UTC ISO-8601 regardless of configured TZ
# ---------------------------------------------------------------------------


def test_workspace_created_at_is_utc(client):
    ws = _make_workspace(client)
    assert _is_utc_iso(ws["created_at"]), f"created_at not UTC ISO-8601: {ws['created_at']!r}"


def test_topic_created_at_is_utc(client):
    ws = _make_workspace(client)
    topic = _make_topic(client, ws["id"])
    assert _is_utc_iso(topic["created_at"]), f"created_at not UTC ISO-8601: {topic['created_at']!r}"


def test_message_created_at_is_utc(tmp_path, monkeypatch):
    """Messages stored via SQLite must have UTC ISO-8601 created_at."""
    import sqlite3 as _sqlite3
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")

    from src.master.db import init_db
    db_path = str(tmp_path / "master_data.db")
    init_db(db_path)

    conn = _sqlite3.connect(db_path)
    conn.row_factory = _sqlite3.Row
    ws_id = str(uuid.uuid4())
    topic_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    now_utc = "2026-05-09T10:00:00Z"
    conn.execute(
        "INSERT INTO workspaces (id, name, repo_url, created_at) VALUES (?, ?, ?, ?)",
        (ws_id, "ws", "https://github.com/x/y", now_utc),
    )
    conn.execute(
        "INSERT INTO topics (id, workspace_id, subject, repo_ref, branch_name, worktree_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (topic_id, ws_id, "t", "main", "main", "/tmp/wt", now_utc),
    )
    conn.execute(
        "INSERT INTO messages (id, topic_id, sender, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (msg_id, topic_id, "user", "hello", now_utc),
    )
    conn.commit()
    conn.close()

    with TestClient(app) as c:
        r = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert msgs, "no messages returned"
    assert _is_utc_iso(msgs[0]["created_at"]), f"created_at not UTC ISO-8601: {msgs[0]['created_at']!r}"


def test_workspace_created_at_utc_when_shanghai(client_shanghai):
    """Storage must be UTC even when the display TZ is Asia/Shanghai (UTC+8)."""
    ws = _make_workspace(client_shanghai)
    created = ws["created_at"]
    assert _is_utc_iso(created), f"created_at not UTC ISO-8601: {created!r}"
    # Sanity: UTC hour should not have been shifted to +8 local time.
    # We just verify the format is correct; the content is always UTC.
    dt = _parse_iso_utc(created)
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_topic_created_at_utc_when_shanghai(client_shanghai):
    ws = _make_workspace(client_shanghai)
    topic = _make_topic(client_shanghai, ws["id"])
    assert _is_utc_iso(topic["created_at"])


def test_event_action_created_at_utc_when_shanghai(client_shanghai):
    ws = _make_workspace(client_shanghai)
    topic = _make_topic(client_shanghai, ws["id"])
    r = client_shanghai.post(
        f"/api/workspaces/{ws['id']}/topics/{topic['id']}/event-actions",
        json={
            "event_type": "topic_message_sent",
            "staff_name": "bot",
            "prompt_template": "Hello {topic_name}",
            "timing": "after",
            "enabled": True,
        },
    )
    assert r.status_code == 201
    ea = r.json()
    assert _is_utc_iso(ea["created_at"])
    assert _is_utc_iso(ea["updated_at"])


# ---------------------------------------------------------------------------
# 3. System-settings API round-trip
# ---------------------------------------------------------------------------


def test_get_system_settings_default_returns_timezone(client):
    r = client.get("/api/config/system-settings")
    assert r.status_code == 200
    body = r.json()
    assert "timezone" in body
    assert body["timezone"]  # non-empty


def test_patch_system_settings_asia_shanghai(client):
    r = client.patch("/api/config/system-settings", json={"timezone": "Asia/Shanghai"})
    assert r.status_code == 200
    assert r.json()["timezone"] == "Asia/Shanghai"


def test_patch_system_settings_invalid_tz_rejected(client):
    r = client.patch("/api/config/system-settings", json={"timezone": "Mars/OlympusMons"})
    assert r.status_code == 422


def test_get_system_settings_returns_configured_tz(client):
    client.patch("/api/config/system-settings", json={"timezone": "Asia/Shanghai"})
    r = client.get("/api/config/system-settings")
    assert r.json()["timezone"] == "Asia/Shanghai"


# ---------------------------------------------------------------------------
# 4. Scheduler interprets cron in configured TZ, stores last_fired_at as UTC
# ---------------------------------------------------------------------------


def test_scheduler_tick_fires_in_configured_tz(tmp_path):
    """Cron '0 9 * * *' in Asia/Shanghai (UTC+8) fires at 01:00 UTC, not 09:00 UTC.

    Arrange: last_fired_at = 2026-05-09T00:58:00Z (08:58 Shanghai — before 09:00).
    Clock:   now           = 2026-05-09T01:02:00Z (09:02 Shanghai — after  09:00).
    Expect:  scheduler fires, last_fired_at advances to 2026-05-09T01:00:00Z.
    """
    from src.master.db import init_db
    from src.master.main import _scheduler_tick

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ws_id = str(uuid.uuid4())
    topic_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())

    # last_fired_at is 00:58 UTC = 08:58 Shanghai — before today's 09:00 firing
    last_fired_at = "2026-05-09T00:58:00Z"
    now_utc = "2026-05-09T01:02:00Z"
    conn.execute(
        "INSERT INTO workspaces (id, name, repo_url, created_at) VALUES (?, ?, ?, ?)",
        (ws_id, "ws", "https://github.com/x/y", now_utc),
    )
    conn.execute(
        "INSERT INTO topics (id, workspace_id, subject, repo_ref, branch_name, worktree_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (topic_id, ws_id, "topic", "main", "main", "/tmp/wt", now_utc),
    )
    conn.execute(
        """INSERT INTO event_actions
           (id, event_type, scope_type, scope_id, staff_name, prompt_template,
            timing, cron_expr, last_fired_at, enabled, created_at, updated_at)
           VALUES (?, 'topic_scheduler', 'topic', ?, 'bot', 'Hello', NULL, ?, ?, 1, ?, ?)""",
        (action_id, topic_id, "0 9 * * *", last_fired_at, now_utc, now_utc),
    )
    conn.commit()
    conn.close()

    # Pre-configure Asia/Shanghai so get_configured_timezone reads it from DB.
    conn2 = sqlite3.connect(db_path)
    conn2.execute(
        "INSERT INTO config (scope_type, scope_id, key, value, updated_at) VALUES ('global', '', 'system.timezone', 'Asia/Shanghai', ?)",
        (now_utc,),
    )
    conn2.commit()
    conn2.close()

    app_state = _make_app_state(db_path)
    emitted: list[dict] = []

    async def run():
        loop = asyncio.get_running_loop()
        app_state.event_loop = loop

        original_put = app_state.event_queue.put_nowait
        def capturing(ev):
            emitted.append(ev)
            original_put(ev)
        app_state.event_queue.put_nowait = capturing

        now_dt = datetime(2026, 5, 9, 1, 2, 0, tzinfo=timezone.utc)
        _scheduler_tick(db_path, app_state, now_dt)

    asyncio.run(run())

    # The action should have fired once.
    assert len(emitted) == 1, f"expected 1 fire, got {len(emitted)}"

    # last_fired_at should have advanced to exactly 01:00 UTC (09:00 Shanghai).
    conn3 = sqlite3.connect(db_path)
    conn3.row_factory = sqlite3.Row
    row = conn3.execute("SELECT last_fired_at FROM event_actions WHERE id=?", (action_id,)).fetchone()
    conn3.close()
    assert row["last_fired_at"] == "2026-05-09T01:00:00Z", (
        f"expected last_fired_at=2026-05-09T01:00:00Z, got {row['last_fired_at']!r}"
    )


def test_scheduler_does_not_fire_before_cron_time_in_tz(tmp_path):
    """Cron '0 9 * * *' in Asia/Shanghai must NOT fire at 00:58 UTC (08:58 Shanghai)."""
    from src.master.db import init_db
    from src.master.main import _scheduler_tick

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ws_id = str(uuid.uuid4())
    topic_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())

    last_fired_at = "2026-05-08T01:00:00Z"  # yesterday's 09:00 Shanghai
    now_utc = "2026-05-09T00:58:00Z"         # 08:58 Shanghai — before today's 09:00

    conn.execute(
        "INSERT INTO workspaces (id, name, repo_url, created_at) VALUES (?, ?, ?, ?)",
        (ws_id, "ws", "https://github.com/x/y", now_utc),
    )
    conn.execute(
        "INSERT INTO topics (id, workspace_id, subject, repo_ref, branch_name, worktree_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (topic_id, ws_id, "topic", "main", "main", "/tmp/wt", now_utc),
    )
    conn.execute(
        """INSERT INTO event_actions
           (id, event_type, scope_type, scope_id, staff_name, prompt_template,
            timing, cron_expr, last_fired_at, enabled, created_at, updated_at)
           VALUES (?, 'topic_scheduler', 'topic', ?, 'bot', 'Hello', NULL, ?, ?, 1, ?, ?)""",
        (action_id, topic_id, "0 9 * * *", last_fired_at, now_utc, now_utc),
    )
    conn.commit()
    conn.close()

    conn2 = sqlite3.connect(db_path)
    conn2.execute(
        "INSERT INTO config (scope_type, scope_id, key, value, updated_at) VALUES ('global', '', 'system.timezone', 'Asia/Shanghai', ?)",
        (now_utc,),
    )
    conn2.commit()
    conn2.close()

    app_state = _make_app_state(db_path)
    emitted: list[dict] = []

    async def run():
        loop = asyncio.get_running_loop()
        app_state.event_loop = loop

        original_put = app_state.event_queue.put_nowait
        def capturing(ev):
            emitted.append(ev)
            original_put(ev)
        app_state.event_queue.put_nowait = capturing

        now_dt = datetime(2026, 5, 9, 0, 58, 0, tzinfo=timezone.utc)
        _scheduler_tick(db_path, app_state, now_dt)

    asyncio.run(run())

    assert len(emitted) == 0, f"expected no fires before 09:00 Shanghai, got {len(emitted)}"
