"""Tests for the topic_archiving vetoable pre-commit interceptor (ADR-0014).

Test plan: docs/test-plans/topic-archiving-veto.md
Design doc: docs/design/vetoable-topic-archiving-event.md (ADR-0014)
"""
from __future__ import annotations

import sqlite3
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agent.mqtt_loop import _extract_verdict
from src.master.db import init_db, get_connection
from src.master.event_dispatcher import VetoResult
from src.master.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI TestClient with an isolated on-disk SQLite DB."""
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def workspace_id(client):
    r = client.post(
        "/api/workspaces",
        json={"name": "test-ws", "repo_url": "https://github.com/x/y"},
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture()
def topic_id(client, workspace_id):
    r = client.post(
        f"/api/workspaces/{workspace_id}/topics",
        json={"subject": "Fix the bug", "repo_ref": "main"},
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture()
def ea_url(workspace_id, topic_id):
    return f"/api/workspaces/{workspace_id}/topics/{topic_id}/event-actions"


def _make_archiving_action(
    *,
    staff_name: str = "test-staff",
    prompt_template: str = "Should topic {topic_name} be archived?",
    enabled: bool = True,
) -> dict:
    return {
        "event_type": "topic_archiving",
        "staff_name": staff_name,
        "prompt_template": prompt_template,
        "timing": None,
        "enabled": enabled,
    }


# ---------------------------------------------------------------------------
# TC-01: No topic_archiving actions → 204, archived_at is set
# ---------------------------------------------------------------------------


def test_tc01_no_actions_returns_204(client, workspace_id, topic_id):
    """DELETE with no topic_archiving actions proceeds immediately → 204."""
    r = client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.status_code == 204


def test_tc01_no_actions_sets_archived_at(client, workspace_id, topic_id):
    """After 204 delete with no actions, archived_at is populated."""
    client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    r = client.get(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None


# ---------------------------------------------------------------------------
# TC-02: Allow path → 204
# ---------------------------------------------------------------------------


def test_tc02_allow_returns_204(client, workspace_id, topic_id, ea_url):
    """When veto_dispatch returns allowed=True, DELETE → 204."""
    # Register a topic_archiving action so veto_dispatch would normally be called
    r = client.post(ea_url, json=_make_archiving_action())
    assert r.status_code == 201

    allow_result = VetoResult(allowed=True, reason="", timed_out=False)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=allow_result)):
        r = client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")

    assert r.status_code == 204


def test_tc02_allow_sets_archived_at(client, workspace_id, topic_id, ea_url):
    """After allowed veto, archived_at is populated."""
    client.post(ea_url, json=_make_archiving_action())

    allow_result = VetoResult(allowed=True, reason="", timed_out=False)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=allow_result)):
        client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")

    r = client.get(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.json()["archived_at"] is not None


# ---------------------------------------------------------------------------
# TC-03: Deny path → 423
# ---------------------------------------------------------------------------


def test_tc03_deny_returns_423(client, workspace_id, topic_id, ea_url):
    """When veto_dispatch returns allowed=False, DELETE → 423."""
    client.post(ea_url, json=_make_archiving_action())

    deny_result = VetoResult(allowed=False, reason="open PRs", timed_out=False)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=deny_result)):
        r = client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")

    assert r.status_code == 423


def test_tc03_deny_body_contains_reason(client, workspace_id, topic_id, ea_url):
    """423 response body carries the veto reason."""
    client.post(ea_url, json=_make_archiving_action())

    deny_result = VetoResult(allowed=False, reason="open PRs", timed_out=False)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=deny_result)):
        r = client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")

    body = r.json()
    assert "reason" in body["detail"]
    assert body["detail"]["reason"] == "open PRs"


def test_tc03_deny_topic_not_archived(client, workspace_id, topic_id, ea_url):
    """After a 423 veto, the topic is NOT archived."""
    client.post(ea_url, json=_make_archiving_action())

    deny_result = VetoResult(allowed=False, reason="open PRs", timed_out=False)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=deny_result)):
        client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")

    r = client.get(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.json()["archived_at"] is None


# ---------------------------------------------------------------------------
# TC-04: Timeout → 504
# ---------------------------------------------------------------------------


def test_tc04_timeout_returns_504(client, workspace_id, topic_id, ea_url):
    """When veto_dispatch times out, DELETE → 504."""
    client.post(ea_url, json=_make_archiving_action())

    timeout_result = VetoResult(allowed=True, reason="", timed_out=True)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=timeout_result)):
        r = client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")

    assert r.status_code == 504


def test_tc04_timeout_topic_not_archived(client, workspace_id, topic_id, ea_url):
    """After a 504 timeout, the topic is NOT archived."""
    client.post(ea_url, json=_make_archiving_action())

    timeout_result = VetoResult(allowed=True, reason="", timed_out=True)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=timeout_result)):
        client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")

    r = client.get(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.json()["archived_at"] is None


# ---------------------------------------------------------------------------
# TC-05: override=true → 204, veto_dispatch NOT called
# ---------------------------------------------------------------------------


def test_tc05_override_returns_204(client, workspace_id, topic_id, ea_url):
    """DELETE ?override=true returns 204 regardless of what veto would return."""
    client.post(ea_url, json=_make_archiving_action())

    deny_result = VetoResult(allowed=False, reason="would deny", timed_out=False)
    mock_veto = AsyncMock(return_value=deny_result)
    with patch("src.master.event_dispatcher.veto_dispatch", new=mock_veto):
        r = client.delete(
            f"/api/workspaces/{workspace_id}/topics/{topic_id}",
            params={"override": "true"},
        )

    assert r.status_code == 204


def test_tc05_override_veto_dispatch_not_called(client, workspace_id, topic_id, ea_url):
    """With override=true, veto_dispatch is never invoked."""
    client.post(ea_url, json=_make_archiving_action())

    deny_result = VetoResult(allowed=False, reason="would deny", timed_out=False)
    mock_veto = AsyncMock(return_value=deny_result)
    with patch("src.master.event_dispatcher.veto_dispatch", new=mock_veto):
        client.delete(
            f"/api/workspaces/{workspace_id}/topics/{topic_id}",
            params={"override": "true"},
        )

    mock_veto.assert_not_called()


def test_tc05_override_sets_archived_at(client, workspace_id, topic_id, ea_url):
    """After override=true delete, archived_at is set."""
    client.post(ea_url, json=_make_archiving_action())

    deny_result = VetoResult(allowed=False, reason="would deny", timed_out=False)
    with patch("src.master.event_dispatcher.veto_dispatch", new=AsyncMock(return_value=deny_result)):
        client.delete(
            f"/api/workspaces/{workspace_id}/topics/{topic_id}",
            params={"override": "true"},
        )

    r = client.get(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.json()["archived_at"] is not None


# ---------------------------------------------------------------------------
# TC-06: event_actions CRUD for topic_archiving
# ---------------------------------------------------------------------------


def test_tc06_create_archiving_action_returns_201(client, ea_url):
    """POST a valid topic_archiving action → 201."""
    r = client.post(ea_url, json=_make_archiving_action())
    assert r.status_code == 201


def test_tc06_create_archiving_action_body(client, ea_url):
    """Response body for a created topic_archiving action has correct fields."""
    r = client.post(ea_url, json=_make_archiving_action(staff_name="my-staff"))
    body = r.json()
    assert body["event_type"] == "topic_archiving"
    assert body["staff_name"] == "my-staff"
    assert body["timing"] is None
    assert body["cron_expr"] is None


def test_tc06_create_archiving_with_cron_returns_422(client, ea_url):
    """topic_archiving with cron_expr must be rejected (422)."""
    action = _make_archiving_action()
    action["cron_expr"] = "* * * * *"
    r = client.post(ea_url, json=action)
    assert r.status_code == 422


def test_tc06_create_archiving_with_timing_before_returns_422(client, ea_url):
    """topic_archiving with timing='before' must be rejected (422)."""
    action = _make_archiving_action()
    action["timing"] = "before"
    r = client.post(ea_url, json=action)
    assert r.status_code == 422


def test_tc06_create_archiving_with_timing_after_returns_201(client, ea_url):
    """topic_archiving with timing='after' is allowed (201)."""
    action = _make_archiving_action()
    action["timing"] = "after"
    r = client.post(ea_url, json=action)
    assert r.status_code == 201


def test_tc06_create_archiving_appears_in_list(client, ea_url):
    """A created topic_archiving action appears in the list endpoint."""
    client.post(ea_url, json=_make_archiving_action())
    r = client.get(ea_url)
    assert r.status_code == 200
    types = [a["event_type"] for a in r.json()]
    assert "topic_archiving" in types


# ---------------------------------------------------------------------------
# TC-07: DB migration — old schema gets upgraded, INSERT succeeds
# ---------------------------------------------------------------------------


def test_tc07_migration_old_schema_upgraded(tmp_path):
    """init_db on a pre-migration DB (no topic_archiving in CHECK) upgrades schema
    so that INSERT of a topic_archiving row succeeds."""
    db_path = str(tmp_path / "old.db")

    # Build the old schema without topic_archiving in the CHECK constraint
    old_schema = """
    CREATE TABLE event_actions (
        id              TEXT PRIMARY KEY,
        event_type      TEXT NOT NULL CHECK (event_type IN (
                            'topic_message_sent',
                            'topic_message_received',
                            'topic_scheduler',
                            'topic_archived'
                        )),
        scope_type      TEXT NOT NULL CHECK (scope_type IN ('topic')),
        scope_id        TEXT NOT NULL,
        staff_name      TEXT NOT NULL,
        prompt_template TEXT NOT NULL,
        timing          TEXT CHECK (timing IN ('before', 'after')),
        cron_expr       TEXT,
        last_fired_at   TEXT,
        last_run_at     TEXT,
        last_run_status TEXT CHECK (last_run_status IN (
                            'ok',
                            'staff_missing',
                            'render_error',
                            'dispatch_error'
                        )),
        last_run_output TEXT,
        enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        CHECK (
            (event_type = 'topic_scheduler'      AND cron_expr IS NOT NULL AND timing IS NULL)
            OR
            (event_type = 'topic_message_sent'   AND cron_expr IS NULL     AND timing IN ('before','after'))
            OR
            (event_type IN ('topic_message_received','topic_archived')
                                                  AND cron_expr IS NULL     AND (timing IS NULL OR timing = 'after'))
        )
    );
    CREATE TABLE workspaces (
        id             TEXT PRIMARY KEY,
        name           TEXT NOT NULL UNIQUE,
        repo_url       TEXT NOT NULL,
        container_name TEXT,
        created_at     TEXT NOT NULL,
        archived_at    TEXT
    );
    CREATE TABLE staffs (
        id          TEXT PRIMARY KEY,
        scope_type  TEXT NOT NULL CHECK (scope_type IN ('global', 'workspace', 'topic')),
        scope_id    TEXT,
        name        TEXT NOT NULL,
        adapter     TEXT NOT NULL DEFAULT 'claude-code',
        model       TEXT,
        system_prompt TEXT,
        agent       TEXT,
        session_scope TEXT NOT NULL DEFAULT 'topic' CHECK (session_scope IN ('topic', 'workspace', 'global')),
        is_default  INTEGER NOT NULL DEFAULT 0,
        extra_flags TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    CREATE TABLE topics (
        id            TEXT PRIMARY KEY,
        workspace_id  TEXT NOT NULL,
        subject       TEXT NOT NULL,
        branch_name   TEXT NOT NULL,
        worktree_path TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        archived_at   TEXT
    );
    """

    # Create the old-schema DB
    conn = sqlite3.connect(db_path)
    conn.executescript(old_schema)
    conn.commit()
    conn.close()

    # Running init_db should trigger _migrate_event_actions_v2
    init_db(db_path)

    # Now INSERT a topic_archiving row — should succeed
    conn = get_connection(db_path)
    try:
        now = "2026-05-09T10:00:00Z"
        action_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO event_actions"
            " (id, event_type, scope_type, scope_id, staff_name, prompt_template,"
            "  timing, cron_expr, enabled, created_at, updated_at)"
            " VALUES (?, 'topic_archiving', 'topic', 'some-topic-id', 'bot',"
            "         'Archive?', NULL, NULL, 1, ?, ?)",
            (action_id, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT event_type FROM event_actions WHERE id = ?", (action_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["event_type"] == "topic_archiving"


def test_tc07_migration_idempotent(tmp_path):
    """Running init_db twice on a fresh DB does not raise."""
    db_path = str(tmp_path / "idempotent.db")
    init_db(db_path)
    init_db(db_path)  # should not raise


def test_tc07_migration_preserves_structured_output_values(tmp_path):
    """Regression for prod 500: `_migrate_event_actions_v2` previously
    recreated the table without `structured_output`, dropping the column and
    any values in it during the same init_db that the `_MIGRATIONS` ALTER
    had just added. Verify the column survives v2 and existing values carry."""
    db_path = str(tmp_path / "with_structured_output.db")

    # Old (pre-topic_archiving) event_actions plus a structured_output column,
    # mirroring the state of a DB that had the staging-fix ALTER applied but
    # was still on schema v1.
    old_schema_with_struct = """
    CREATE TABLE event_actions (
        id              TEXT PRIMARY KEY,
        event_type      TEXT NOT NULL CHECK (event_type IN (
                            'topic_message_sent',
                            'topic_message_received',
                            'topic_scheduler',
                            'topic_archived'
                        )),
        scope_type      TEXT NOT NULL CHECK (scope_type IN ('topic')),
        scope_id        TEXT NOT NULL,
        staff_name      TEXT NOT NULL,
        prompt_template TEXT NOT NULL,
        timing          TEXT CHECK (timing IN ('before', 'after')),
        cron_expr       TEXT,
        last_fired_at   TEXT,
        last_run_at     TEXT,
        last_run_status TEXT,
        last_run_output TEXT,
        enabled         INTEGER NOT NULL DEFAULT 1,
        structured_output INTEGER DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, repo_url TEXT NOT NULL,
        container_name TEXT, created_at TEXT NOT NULL, archived_at TEXT
    );
    CREATE TABLE staffs (
        id TEXT PRIMARY KEY, scope_type TEXT NOT NULL, scope_id TEXT,
        name TEXT NOT NULL, adapter TEXT NOT NULL DEFAULT 'claude-code',
        model TEXT, system_prompt TEXT, agent TEXT,
        session_scope TEXT NOT NULL DEFAULT 'topic', is_default INTEGER NOT NULL DEFAULT 0,
        extra_flags TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE topics (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, subject TEXT NOT NULL,
        branch_name TEXT NOT NULL, worktree_path TEXT NOT NULL,
        created_at TEXT NOT NULL, archived_at TEXT
    );
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(old_schema_with_struct)
    now = "2026-05-10T10:00:00Z"
    keeps_struct_on = str(uuid.uuid4())
    keeps_struct_off = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO event_actions"
        " (id, event_type, scope_type, scope_id, staff_name, prompt_template,"
        "  timing, enabled, structured_output, created_at, updated_at)"
        " VALUES (?, 'topic_message_sent', 'topic', 't1', 'bot', 'p',"
        "         'before', 1, 1, ?, ?)",
        (keeps_struct_on, now, now),
    )
    conn.execute(
        "INSERT INTO event_actions"
        " (id, event_type, scope_type, scope_id, staff_name, prompt_template,"
        "  timing, enabled, structured_output, created_at, updated_at)"
        " VALUES (?, 'topic_message_sent', 'topic', 't1', 'bot', 'p',"
        "         'before', 1, 0, ?, ?)",
        (keeps_struct_off, now, now),
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(event_actions)")}
        assert "structured_output" in cols, "v2 must preserve structured_output column"
        rows = {
            r["id"]: r["structured_output"]
            for r in conn.execute(
                "SELECT id, structured_output FROM event_actions WHERE id IN (?, ?)",
                (keeps_struct_on, keeps_struct_off),
            )
        }
        assert rows[keeps_struct_on] == 1, "structured_output=1 must survive v2"
        assert rows[keeps_struct_off] == 0, "structured_output=0 must survive v2"
    finally:
        conn.close()


def test_tc07_migration_recovers_lost_structured_output(tmp_path):
    """Regression for prod 500: a DB that already ran the *broken* v2
    (event_actions has `topic_archiving` but no `structured_output`) must
    recover the column on the next init_db, restoring the message-send path.
    The fix relies on `_MIGRATIONS` ALTER adding the column; v2 then short-circuits."""
    db_path = str(tmp_path / "post_broken_v2.db")

    # Schema matches what `_migrate_event_actions_v2` produced before the fix:
    # topic_archiving is in the event_type CHECK, but structured_output is absent.
    post_broken_v2 = """
    CREATE TABLE event_actions (
        id              TEXT PRIMARY KEY,
        event_type      TEXT NOT NULL CHECK (event_type IN (
                            'topic_message_sent',
                            'topic_message_received',
                            'topic_scheduler',
                            'topic_archived',
                            'topic_archiving'
                        )),
        scope_type      TEXT NOT NULL CHECK (scope_type IN ('topic')),
        scope_id        TEXT NOT NULL,
        staff_name      TEXT NOT NULL,
        prompt_template TEXT NOT NULL,
        timing          TEXT CHECK (timing IN ('before', 'after')),
        cron_expr       TEXT,
        last_fired_at   TEXT,
        last_run_at     TEXT,
        last_run_status TEXT,
        last_run_output TEXT,
        enabled         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, repo_url TEXT NOT NULL,
        container_name TEXT, created_at TEXT NOT NULL, archived_at TEXT
    );
    CREATE TABLE staffs (
        id TEXT PRIMARY KEY, scope_type TEXT NOT NULL, scope_id TEXT,
        name TEXT NOT NULL, adapter TEXT NOT NULL DEFAULT 'claude-code',
        model TEXT, system_prompt TEXT, agent TEXT,
        session_scope TEXT NOT NULL DEFAULT 'topic', is_default INTEGER NOT NULL DEFAULT 0,
        extra_flags TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE topics (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, subject TEXT NOT NULL,
        branch_name TEXT NOT NULL, worktree_path TEXT NOT NULL,
        created_at TEXT NOT NULL, archived_at TEXT
    );
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(post_broken_v2)
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = get_connection(db_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(event_actions)")}
        assert "structured_output" in cols, (
            "init_db on a post-broken-v2 DB must restore structured_output"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TC-08: _extract_verdict — inline JSON
# ---------------------------------------------------------------------------


def test_tc08_extract_verdict_inline_json_deny():
    """Pure JSON payload with verdict=deny is returned as-is."""
    result = _extract_verdict('{"verdict":"deny","reason":"open PRs"}')
    assert result["verdict"] == "deny"
    assert result["reason"] == "open PRs"


def test_tc08_extract_verdict_inline_json_allow():
    """Pure JSON payload with verdict=allow is returned as-is."""
    result = _extract_verdict('{"verdict":"allow","reason":"looks good"}')
    assert result["verdict"] == "allow"
    assert result["reason"] == "looks good"


# ---------------------------------------------------------------------------
# TC-09: _extract_verdict — trailing JSON in prose
# ---------------------------------------------------------------------------


def test_tc09_extract_verdict_trailing_json():
    """LLM prose with a trailing JSON object: verdict extracted correctly."""
    text = (
        "I have reviewed the topic. There are several open pull requests that "
        "still need review, so archiving should be blocked at this time. "
        '{"verdict":"deny","reason":"open PRs still need review"}'
    )
    result = _extract_verdict(text)
    assert result["verdict"] == "deny"
    assert "open PRs" in result["reason"]


def test_tc09_extract_verdict_embedded_json():
    """JSON object embedded mid-text: verdict extracted from it."""
    text = (
        'After careful consideration {"verdict":"allow","reason":"all clear"} I recommend proceeding.'
    )
    result = _extract_verdict(text)
    assert result["verdict"] == "allow"


def test_tc09_extract_verdict_last_json_wins():
    """When multiple JSON objects are present, the last valid verdict wins."""
    text = (
        '{"verdict":"allow","reason":"first"} some text '
        '{"verdict":"deny","reason":"second is last"}'
    )
    result = _extract_verdict(text)
    # _extract_verdict scans in reversed order, so the last JSON object wins
    assert result["verdict"] == "deny"
    assert result["reason"] == "second is last"


# ---------------------------------------------------------------------------
# TC-10: _extract_verdict — no JSON → allow fallback
# ---------------------------------------------------------------------------


def test_tc10_extract_verdict_no_json_returns_allow():
    """Plain text with no JSON object falls back to allow."""
    result = _extract_verdict("Everything looks good, go ahead and archive it.")
    assert result["verdict"] == "allow"
    assert "no verdict found" in result["reason"]


def test_tc10_extract_verdict_empty_string():
    """Empty string falls back to allow."""
    result = _extract_verdict("")
    assert result["verdict"] == "allow"


def test_tc10_extract_verdict_json_without_verdict_key():
    """JSON object without a 'verdict' key falls back to allow."""
    result = _extract_verdict('{"some":"other","keys":"here"}')
    assert result["verdict"] == "allow"


def test_tc10_extract_verdict_invalid_verdict_value():
    """JSON object with an unrecognised verdict value falls back to allow."""
    result = _extract_verdict('{"verdict":"maybe","reason":"unsure"}')
    assert result["verdict"] == "allow"
