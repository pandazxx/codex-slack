"""Unit tests for chunk persistence helpers in src/master/mqtt_client.py.

Covers:
- _save_chunk inserts a row with correct fields
- _save_chunk with a retry event DELETEs all prior rows for that message_id
  then inserts the retry row
- _save_agent_response atomically inserts into messages AND deletes chunks;
  mid-transaction failure rolls back both operations

These tests create a real SQLite DB in tmp_path with the chunks table present.
Because the chunks table is added in the streaming feature branch and may not
exist in the base schema yet, the tests create it directly if init_db does not
produce it.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch
import logging

import pytest

from src.master.db import init_db

# ---------------------------------------------------------------------------
# Import guard — skip the whole module if _save_chunk is not yet implemented.
# ---------------------------------------------------------------------------
try:
    from src.master.mqtt_client import _save_chunk, _save_agent_response
    _CHUNKS_IMPLEMENTED = True
except ImportError:
    _CHUNKS_IMPLEMENTED = False

pytestmark = pytest.mark.skipif(
    not _CHUNKS_IMPLEMENTED,
    reason="_save_chunk not yet implemented; skipping chunk persistence tests",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CHUNKS_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL,
    topic_id    TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event       TEXT NOT NULL,
    created_at  REAL NOT NULL DEFAULT (unixepoch('now','subsec'))
);
CREATE INDEX IF NOT EXISTS idx_chunks_message ON chunks (message_id, seq);
CREATE INDEX IF NOT EXISTS idx_chunks_topic   ON chunks (topic_id);
"""

_TOPICS_SEED = """
INSERT OR IGNORE INTO workspaces (id, name, repo_url, container_name, created_at, archived_at)
VALUES ('ws1', 'Test WS', 'https://github.com/x/y', NULL, '2026-01-01T00:00:00Z', NULL);

INSERT OR IGNORE INTO topics (id, workspace_id, subject, branch_name, worktree_path, created_at)
VALUES ('t1', 'ws1', 'Test topic', 'feat/t1', '/tmp/wt1', '2026-01-01T00:00:00Z');
"""


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    # Ensure the chunks table exists (may not be in base schema yet)
    conn = sqlite3.connect(path)
    conn.executescript(_CHUNKS_DDL)
    conn.executescript(_TOPICS_SEED)
    conn.commit()
    conn.close()
    return path


def _count_chunks(db_path: str, message_id: str) -> int:
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE message_id = ?", (message_id,)
    ).fetchone()[0]
    conn.close()
    return count


def _fetch_chunks(db_path: str, message_id: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM chunks WHERE message_id = ? ORDER BY seq", (message_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count_messages(db_path: str, message_id: str) -> int:
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE id = ?", (message_id,)
    ).fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# _save_chunk — happy path
# ---------------------------------------------------------------------------

def test_save_chunk_inserts_row(db_path):
    payload = {
        "message_id": "msg-001",
        "agent_name": "claude",
        "seq": 0,
        "event": {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello"}]}},
    }
    _save_chunk(db_path, "t1", payload)
    assert _count_chunks(db_path, "msg-001") == 1


def test_save_chunk_stores_correct_fields(db_path):
    event = {"type": "system", "subtype": "task_progress", "description": "Running ls"}
    payload = {"message_id": "msg-002", "agent_name": "claude", "seq": 3, "event": event}
    _save_chunk(db_path, "t1", payload)

    rows = _fetch_chunks(db_path, "msg-002")
    assert len(rows) == 1
    row = rows[0]
    assert row["message_id"] == "msg-002"
    assert row["topic_id"] == "t1"
    assert row["seq"] == 3
    assert json.loads(row["event"]) == event


def test_save_chunk_multiple_rows_ordered_by_seq(db_path):
    for seq in range(5):
        payload = {
            "message_id": "msg-003",
            "agent_name": "claude",
            "seq": seq,
            "event": {"type": "assistant", "seq_label": seq},
        }
        _save_chunk(db_path, "t1", payload)

    rows = _fetch_chunks(db_path, "msg-003")
    assert len(rows) == 5
    seqs = [r["seq"] for r in rows]
    assert seqs == list(range(5))


def test_save_chunk_different_message_ids_are_independent(db_path):
    for mid in ["msg-a", "msg-b"]:
        for seq in range(3):
            _save_chunk(db_path, "t1", {
                "message_id": mid, "agent_name": "claude",
                "seq": seq, "event": {"type": "assistant"},
            })

    assert _count_chunks(db_path, "msg-a") == 3
    assert _count_chunks(db_path, "msg-b") == 3


# ---------------------------------------------------------------------------
# _save_chunk — retry event deletes prior rows then inserts the retry row
# ---------------------------------------------------------------------------

def test_save_chunk_retry_deletes_prior_rows(db_path):
    """When the event is {type:system, subtype:retry}, all prior rows for that
    message_id must be deleted before the retry row is inserted."""
    # Insert 4 normal chunks first
    for seq in range(4):
        _save_chunk(db_path, "t1", {
            "message_id": "msg-retry",
            "agent_name": "claude",
            "seq": seq,
            "event": {"type": "assistant"},
        })
    assert _count_chunks(db_path, "msg-retry") == 4

    # Now send the retry chunk
    _save_chunk(db_path, "t1", {
        "message_id": "msg-retry",
        "agent_name": "claude",
        "seq": 4,
        "event": {"type": "system", "subtype": "retry"},
    })

    # Only the retry row remains
    rows = _fetch_chunks(db_path, "msg-retry")
    assert len(rows) == 1
    assert json.loads(rows[0]["event"]) == {"type": "system", "subtype": "retry"}
    assert rows[0]["seq"] == 4


def test_save_chunk_retry_does_not_delete_other_message_ids(db_path):
    """The DELETE triggered by a retry chunk must be scoped to the message_id."""
    _save_chunk(db_path, "t1", {
        "message_id": "msg-bystander",
        "agent_name": "claude",
        "seq": 0,
        "event": {"type": "assistant"},
    })
    _save_chunk(db_path, "t1", {
        "message_id": "msg-retrying",
        "agent_name": "claude",
        "seq": 0,
        "event": {"type": "system", "subtype": "retry"},
    })

    # Bystander must be unaffected
    assert _count_chunks(db_path, "msg-bystander") == 1


# ---------------------------------------------------------------------------
# _save_chunk — DB failure swallowed; does not propagate
# ---------------------------------------------------------------------------

def test_save_chunk_db_failure_does_not_raise(db_path):
    """A DB error inside _save_chunk must not propagate to the caller."""
    bad_db_path = "/nonexistent/path/test.db"
    # Should not raise
    _save_chunk(bad_db_path, "t1", {
        "message_id": "msg-fail",
        "agent_name": "claude",
        "seq": 0,
        "event": {"type": "assistant"},
    })


def test_save_chunk_db_failure_logs_error(db_path, caplog):
    """A DB error inside _save_chunk must emit a log record."""
    bad_db_path = "/nonexistent/path/test.db"
    with caplog.at_level(logging.ERROR, logger="src.master.mqtt_client"):
        _save_chunk(bad_db_path, "t1", {
            "message_id": "msg-log",
            "agent_name": "claude",
            "seq": 0,
            "event": {"type": "assistant"},
        })
    # Some log record at WARNING or ERROR level should mention save_chunk
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert records, "Expected at least one WARNING/ERROR log record from _save_chunk failure"


# ---------------------------------------------------------------------------
# _save_agent_response — atomic INSERT messages + DELETE chunks
# ---------------------------------------------------------------------------

def test_save_agent_response_inserts_message_and_deletes_chunks(db_path):
    """After _save_agent_response the messages row exists and the chunks are gone."""
    for seq in range(3):
        _save_chunk(db_path, "t1", {
            "message_id": "msg-resp",
            "agent_name": "claude",
            "seq": seq,
            "event": {"type": "assistant"},
        })
    assert _count_chunks(db_path, "msg-resp") == 3

    payload = {
        "message_id": "msg-resp",
        "agent_name": "claude",
        "reply_to": "user-msg-1",
        "last_response": "The answer",
        "transcript": json.dumps([{"type": "result", "result": "The answer"}]),
        "session_id": "sess-new",
    }
    _save_agent_response(db_path, "t1", payload)

    assert _count_messages(db_path, "msg-resp") == 1
    assert _count_chunks(db_path, "msg-resp") == 0


def test_save_agent_response_does_not_delete_other_message_chunks(db_path):
    """Only chunks matching the message_id in /response are deleted."""
    for mid in ("msg-done", "msg-still-running"):
        for seq in range(2):
            _save_chunk(db_path, "t1", {
                "message_id": mid,
                "agent_name": "claude",
                "seq": seq,
                "event": {"type": "assistant"},
            })

    payload = {
        "message_id": "msg-done",
        "agent_name": "claude",
        "reply_to": "u1",
        "last_response": "finished",
        "transcript": None,
        "session_id": None,
    }
    _save_agent_response(db_path, "t1", payload)

    assert _count_chunks(db_path, "msg-done") == 0
    assert _count_chunks(db_path, "msg-still-running") == 2


def test_save_agent_response_atomicity_on_failure(db_path):
    """If the transaction fails after the INSERT but before the DELETE (or vice
    versa), neither change should be committed — both or neither."""
    # Seed chunks
    for seq in range(2):
        _save_chunk(db_path, "t1", {
            "message_id": "msg-atomic",
            "agent_name": "claude",
            "seq": seq,
            "event": {"type": "assistant"},
        })

    original_connect = sqlite3.connect

    class FailingConn:
        """Wraps a real connection but raises on execute after the INSERT."""
        def __init__(self, path: str):
            self._inner = original_connect(path)
            self._inner.row_factory = sqlite3.Row
            self._execute_count = 0

        def execute(self, sql: str, params=()):
            self._execute_count += 1
            result = self._inner.execute(sql, params)
            # Raise after the INSERT into messages to simulate mid-transaction failure
            if "INSERT" in sql.upper() and "messages" in sql.lower():
                raise sqlite3.OperationalError("simulated disk full")
            return result

        def commit(self):
            self._inner.commit()

        def close(self):
            self._inner.close()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._inner.rollback()

        @property
        def row_factory(self):
            return self._inner.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._inner.row_factory = value

    # We call _save_agent_response with the real db so prior state is valid;
    # we monkeypatch sqlite3.connect to inject a failing connection.
    import src.master.mqtt_client as mqtt_mod

    failing_conn_instance = FailingConn(db_path)

    with patch.object(sqlite3, "connect", return_value=failing_conn_instance):
        try:
            _save_agent_response(db_path, "t1", {
                "message_id": "msg-atomic",
                "agent_name": "claude",
                "reply_to": "u1",
                "last_response": "done",
                "transcript": None,
                "session_id": None,
            })
        except Exception:
            pass  # failure is expected; we check the state below

    # Re-check state using real connection
    assert _count_messages(db_path, "msg-atomic") == 0, \
        "messages row must not be committed if transaction failed"
    assert _count_chunks(db_path, "msg-atomic") == 2, \
        "chunks must not be deleted if transaction failed"
