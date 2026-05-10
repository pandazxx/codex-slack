from __future__ import annotations

import sqlite3
import time
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.db import init_db
from src.master.main import (
    app,
    _db_path,
    _STATIC_DIR,
    _close_stale_streams,
    _ping_for_alive,
    _STALE_STREAM_TIMEOUT_SECONDS,
    _PING_TIMEOUT_SECONDS,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_schema_lists_all_tables(client):
    from src.master.db import TABLES
    r = client.get("/schema")
    assert r.status_code == 200
    assert set(r.json().keys()) == set(TABLES)


def test_schema_workspaces_columns(client):
    r = client.get("/schema")
    cols = r.json()["workspaces"]
    assert "id" in cols
    assert "name" in cols
    assert "repo_url" in cols
    assert "created_at" in cols


def test_schema_staffs_columns(client):
    r = client.get("/schema")
    cols = r.json()["staffs"]
    for expected in ("id", "scope_type", "scope_id", "name", "adapter", "model",
                     "system_prompt", "agent", "session_scope", "is_default"):
        assert expected in cols, f"missing column: {expected}"


def test_db_file_created_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app):
        db = tmp_path / "master_data.db"
        assert db.exists(), "master_data.db was not created on startup"


def test_spa_returns_404_when_not_built(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    fake_static = tmp_path / "static"
    fake_static.mkdir()
    monkeypatch.setattr("src.master.main._STATIC_DIR", fake_static)
    with TestClient(app) as c:
        r = c.get("/some/spa/route")
        assert r.status_code == 404


def test_spa_serves_index_when_built(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    fake_static = tmp_path / "static"
    fake_static.mkdir()
    (fake_static / "index.html").write_text("<!doctype html><html></html>")
    monkeypatch.setattr("src.master.main._STATIC_DIR", fake_static)
    with TestClient(app) as c:
        r = c.get("/any/spa/path")
        assert r.status_code == 200
        assert "html" in r.text


# ---------------------------------------------------------------------------
# _close_stale_streams tests
# ---------------------------------------------------------------------------

_SEED_SQL = """
INSERT OR IGNORE INTO workspaces
    (id, name, repo_url, container_name, created_at)
VALUES ('ws1', 'WS', 'https://github.com/x/y', 'agent-ws1', '2026-01-01T00:00:00Z');

INSERT OR IGNORE INTO topics
    (id, workspace_id, subject, branch_name, worktree_path, created_at)
VALUES ('t1', 'ws1', 'Topic', 'feat/t1', '/tmp/wt1', '2026-01-01T00:00:00Z');
"""


@pytest.fixture()
def stale_db(tmp_path):
    path = str(tmp_path / "stale_test.db")
    init_db(path)
    conn = sqlite3.connect(path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    return path


def _seed_chunks(db_path, message_id, topic_id="t1", agent_name="claude", age_seconds=0):
    """Insert two chunks for message_id with created_at set to now - age_seconds."""
    created_at = time.time() - age_seconds
    conn = sqlite3.connect(db_path)
    for seq in range(2):
        conn.execute(
            "INSERT INTO chunks (message_id, topic_id, seq, event, agent_name, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, topic_id, seq, '{"type":"assistant"}', agent_name, created_at),
        )
    conn.commit()
    conn.close()


def _count_chunks(db_path, message_id):
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM chunks WHERE message_id=?", (message_id,)).fetchone()[0]
    conn.close()
    return n


def _get_message(db_path, message_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _make_settings(dry_run=True):
    return SimpleNamespace(dry_run=dry_run)


def test_close_stale_streams_no_orphans(stale_db):
    """No chunks in DB → function exits cleanly with no DB changes."""
    settings = _make_settings()
    _close_stale_streams(stale_db, settings)
    # No messages should have been inserted
    conn = sqlite3.connect(stale_db)
    count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    assert count == 0


def test_close_stale_streams_container_stopped_marks_interrupted(stale_db):
    """Orphaned chunks with a stopped container → message inserted as interrupted."""
    _seed_chunks(stale_db, "msg-dead")
    settings = _make_settings(dry_run=False)

    with patch("src.master.main.get_container_status", return_value={"status": "exited", "exit_code": 1}):
        _close_stale_streams(stale_db, settings)

    msg = _get_message(stale_db, "msg-dead")
    assert msg is not None
    assert msg["text"] == "(message interrupted)"
    assert msg["sender"] == "agent"
    assert _count_chunks(stale_db, "msg-dead") == 0


def test_close_stale_streams_container_running_recent_chunks_skipped(stale_db):
    """Orphaned chunks with a running container and recent chunks → not stale."""
    _seed_chunks(stale_db, "msg-live", age_seconds=30)  # very recent
    settings = _make_settings(dry_run=False)

    with patch("src.master.main.get_container_status", return_value={"status": "running"}):
        _close_stale_streams(stale_db, settings)

    assert _get_message(stale_db, "msg-live") is None
    assert _count_chunks(stale_db, "msg-live") == 2


def test_close_stale_streams_timeout_marks_interrupted_even_when_running(stale_db):
    """Chunks older than STALE_STREAM_TIMEOUT_SECONDS are stale even if container is running."""
    old_age = _STALE_STREAM_TIMEOUT_SECONDS + 60
    _seed_chunks(stale_db, "msg-old", age_seconds=old_age)
    settings = _make_settings(dry_run=False)

    with patch("src.master.main.get_container_status", return_value={"status": "running"}):
        _close_stale_streams(stale_db, settings)

    msg = _get_message(stale_db, "msg-old")
    assert msg is not None
    assert msg["text"] == "(message interrupted)"
    assert _count_chunks(stale_db, "msg-old") == 0


def test_close_stale_streams_broadcasts_ws_message(stale_db):
    """When a stale stream is closed, a WS message event is broadcast."""
    _seed_chunks(stale_db, "msg-bcast")
    settings = _make_settings(dry_run=False)

    hub = MagicMock()
    loop = MagicMock()
    app_state = SimpleNamespace(hub=hub, event_loop=loop)

    with patch("src.master.main.get_container_status", return_value={"status": "exited", "exit_code": 1}):
        _close_stale_streams(stale_db, settings, app_state)

    hub.broadcast_threadsafe.assert_called_once()
    call_args = hub.broadcast_threadsafe.call_args
    payload = call_args[0][1]
    assert payload["type"] == "message"
    assert payload["message_id"] == "msg-bcast"
    assert payload["last_response"] == "(message interrupted)"


def test_close_stale_streams_already_finalized_not_duplicated(stale_db):
    """If a message row already exists, INSERT OR IGNORE prevents a duplicate."""
    _seed_chunks(stale_db, "msg-dup")
    # Pre-insert a final message to simulate a race where the agent responded
    conn = sqlite3.connect(stale_db)
    conn.execute(
        "INSERT INTO messages (id, topic_id, sender, agent_name, text, created_at)"
        " VALUES ('msg-dup', 't1', 'agent', 'claude', 'The real answer', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    # The LEFT JOIN in the query means this message_id won't appear as orphaned
    settings = _make_settings(dry_run=False)
    with patch("src.master.main.get_container_status", return_value={"status": "exited", "exit_code": 1}):
        _close_stale_streams(stale_db, settings)

    msg = _get_message(stale_db, "msg-dup")
    assert msg["text"] == "The real answer"  # original text preserved


def test_close_stale_streams_dry_run_uses_dry_run_status(stale_db):
    """In dry_run mode get_container_status returns 'dry_run' status — treated as not running."""
    _seed_chunks(stale_db, "msg-dry")
    settings = _make_settings(dry_run=True)

    # dry_run=True means get_container_status returns {"status": "dry_run", ...}
    # That is not "running", so should be treated as stale
    _close_stale_streams(stale_db, settings)

    msg = _get_message(stale_db, "msg-dry")
    assert msg is not None
    assert msg["text"] == "(message interrupted)"


# ---------------------------------------------------------------------------
# Ping-based staleness tests
# ---------------------------------------------------------------------------

def _make_app_state_with_mqtt(mqtt_client):
    """Build a minimal app_state SimpleNamespace with the given mqtt client."""
    import threading
    state = SimpleNamespace(
        hub=MagicMock(),
        event_loop=MagicMock(),
        mqtt=mqtt_client,
        pending_pings={},
    )
    return state


def test_close_stale_streams_ping_alive_skips(stale_db):
    """Container running + ping returns alive=True → stream NOT closed."""
    _seed_chunks(stale_db, "msg-live-ping", age_seconds=30)
    settings = _make_settings(dry_run=False)

    mqtt_client = MagicMock()

    with patch("src.master.main.get_container_status", return_value={"status": "running"}):
        with patch("src.master.main._ping_for_alive", return_value=True):
            app_state = _make_app_state_with_mqtt(mqtt_client)
            _close_stale_streams(stale_db, settings, app_state)

    assert _get_message(stale_db, "msg-live-ping") is None
    assert _count_chunks(stale_db, "msg-live-ping") == 2


def test_close_stale_streams_ping_dead_closes(stale_db):
    """Container running + ping returns alive=False → stream closed."""
    _seed_chunks(stale_db, "msg-dead-ping", age_seconds=30)
    settings = _make_settings(dry_run=False)

    mqtt_client = MagicMock()

    with patch("src.master.main.get_container_status", return_value={"status": "running"}):
        with patch("src.master.main._ping_for_alive", return_value=False):
            app_state = _make_app_state_with_mqtt(mqtt_client)
            _close_stale_streams(stale_db, settings, app_state)

    msg = _get_message(stale_db, "msg-dead-ping")
    assert msg is not None
    assert msg["text"] == "(message interrupted)"
    assert _count_chunks(stale_db, "msg-dead-ping") == 0


def test_close_stale_streams_ping_timeout_closes(stale_db):
    """Container running + no pong within timeout → stream closed."""
    import src.master.main as main_module

    _seed_chunks(stale_db, "msg-timeout-ping", age_seconds=30)
    settings = _make_settings(dry_run=False)

    mqtt_client = MagicMock()

    with patch("src.master.main.get_container_status", return_value={"status": "running"}):
        with patch.object(main_module, "_PING_TIMEOUT_SECONDS", 0.1):
            # mqtt_client.publish does nothing; no pong arrives → event never set → timeout
            app_state = _make_app_state_with_mqtt(mqtt_client)
            _close_stale_streams(stale_db, settings, app_state)

    msg = _get_message(stale_db, "msg-timeout-ping")
    assert msg is not None
    assert msg["text"] == "(message interrupted)"
    assert _count_chunks(stale_db, "msg-timeout-ping") == 0
