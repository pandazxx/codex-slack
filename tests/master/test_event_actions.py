"""Scaffolding for event-based staff action tests.

Test plan: docs/test-plans/event-based-staff-action.md
Design doc: docs/design/event-based-staff-action.md
ADR: docs/decisions/0013-event-based-staff-action.md

Test bodies are NOT yet filled in.  The engineer is implementing public
interfaces in parallel; bodies will be written once the following are stable:

  - dispatch_to_staff signature  (§3 of design doc)
  - emit_event signature         (§4)
  - event_worker / _handle_event (§4)
  - _scheduler_tick              (§6)
  - render_template              (§2)
  - EventActionIn / EventActionOut Pydantic models (§7)
  - REST endpoint paths          (§7)

Each placeholder is annotated with which interface it waits on.
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.main import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW_UTC = "2026-05-08T09:00:00Z"
_CRON_EVERY_MINUTE = "* * * * *"
_CRON_DAILY_9 = "0 9 * * *"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI TestClient with an isolated on-disk SQLite DB.

    Mirrors the pattern in test_staffs.py and test_topics.py.
    MASTER_DRY_RUN=true prevents real container/MQTT side-effects.
    """
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client_mqtt(tmp_path, monkeypatch):
    """TestClient that also exposes the mock MQTT client.

    Use this fixture when a test needs to assert on mqtt.publish calls.
    Yields (client, mock_mqtt).
    """
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            yield c, mock_mqtt


@pytest.fixture()
def workspace_id(client):
    """A freshly created workspace id."""
    r = client.post(
        "/api/workspaces",
        json={"name": "test-workspace", "repo_url": "https://github.com/x/y"},
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture()
def topic_id(client, workspace_id):
    """A freshly created topic id."""
    r = client.post(
        f"/api/workspaces/{workspace_id}/topics",
        json={"subject": "test topic", "repo_ref": "main"},
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture()
def ea_base_url(workspace_id, topic_id):
    """Base URL for event-actions CRUD on the fixture topic."""
    return f"/api/workspaces/{workspace_id}/topics/{topic_id}/event-actions"


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _make_action(
    *,
    event_type: str = "topic_message_sent",
    staff_name: str = "test-staff",
    prompt_template: str = "Hello {topic_name}",
    timing: str | None = "before",
    cron_expr: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Return a dict suitable for POST /event-actions.

    Sensible defaults are chosen so most tests only need to override one field.
    The default produces a valid topic_message_sent action.
    """
    return {
        "event_type": event_type,
        "staff_name": staff_name,
        "prompt_template": prompt_template,
        "timing": timing,
        "cron_expr": cron_expr,
        "enabled": enabled,
    }


def _make_scheduler_action(
    *,
    staff_name: str = "test-staff",
    prompt_template: str = "Daily digest for {topic_name} in {workspace_name}",
    cron_expr: str = _CRON_EVERY_MINUTE,
    enabled: bool = True,
) -> dict[str, Any]:
    """Convenience builder for topic_scheduler actions."""
    return _make_action(
        event_type="topic_scheduler",
        staff_name=staff_name,
        prompt_template=prompt_template,
        timing=None,
        cron_expr=cron_expr,
        enabled=enabled,
    )


def _make_received_action(
    *,
    staff_name: str = "test-staff",
    prompt_template: str = "Agent said: {msgbody}",
    enabled: bool = True,
) -> dict[str, Any]:
    """Convenience builder for topic_message_received actions."""
    return _make_action(
        event_type="topic_message_received",
        staff_name=staff_name,
        prompt_template=prompt_template,
        timing=None,
        cron_expr=None,
        enabled=enabled,
    )


def _make_archived_action(
    *,
    staff_name: str = "test-staff",
    prompt_template: str = "Summarise {topic_name}",
    enabled: bool = True,
) -> dict[str, Any]:
    """Convenience builder for topic_archived actions."""
    return _make_action(
        event_type="topic_archived",
        staff_name=staff_name,
        prompt_template=prompt_template,
        timing=None,
        cron_expr=None,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Frozen-clock fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def frozen_utc():
    """Return a fixed UTC-aware datetime for scheduler tests.

    Usage in test:
        def test_something(frozen_utc):
            now = frozen_utc  # a datetime(2026, 5, 8, 9, 0, 0, tzinfo=timezone.utc)
    """
    return datetime(2026, 5, 8, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def frozen_utc_65s_ago():
    """UTC time representing 65 seconds in the past (action due for * * * * * cron)."""
    base = datetime(2026, 5, 8, 9, 0, 0, tzinfo=timezone.utc)
    return base - timedelta(seconds=65)


@pytest.fixture()
def frozen_utc_5s_ago():
    """UTC time representing 5 seconds in the past (action NOT due for * * * * * cron)."""
    base = datetime(2026, 5, 8, 9, 0, 0, tzinfo=timezone.utc)
    return base - timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Mock MQTT publish recorder
# ---------------------------------------------------------------------------


class MqttRecorder:
    """Captures all calls to mqtt.publish for assertion in tests.

    Usage:
        recorder = MqttRecorder()
        monkeypatch.setattr(mqtt_client_instance, 'publish', recorder.publish)
        # ... trigger dispatch ...
        assert recorder.topic_count("prompt") == 2
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # [(topic, payload_str), ...]

    def publish(self, topic: str, payload: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((topic, payload))

    @property
    def publish_count(self) -> int:
        return len(self.calls)

    def topic_count(self, substring: str) -> int:
        """Count publish calls whose MQTT topic contains `substring`."""
        return sum(1 for t, _ in self.calls if substring in t)

    def payloads(self) -> list[str]:
        return [p for _, p in self.calls]

    def reset(self) -> None:
        self.calls.clear()


@pytest.fixture()
def mqtt_recorder():
    """Return a fresh MqttRecorder.  Wire it in tests via monkeypatch."""
    return MqttRecorder()


# ---------------------------------------------------------------------------
# Minimal app_state stub
# ---------------------------------------------------------------------------


def _make_app_state(*, db_path: str) -> MagicMock:
    """Build a minimal app_state MagicMock for worker/emit tests.

    Provides:
      - event_queue: real asyncio.Queue
      - event_loop:  the running loop (caller must set inside an async context)
      - event_worker_last_progress: None
      - db_path: str
      - hub: MagicMock
      - mqtt: MagicMock

    Note: event_loop must be set by the test after the loop is running, e.g.:
        app_state.event_loop = asyncio.get_event_loop()
    """
    state = MagicMock()
    state.event_queue = asyncio.Queue()
    state.event_worker_last_progress = None
    state.db_path = db_path
    state.hub = MagicMock()
    state.mqtt = MagicMock()
    # event_loop will be set by the test body once a loop is available.
    return state


# ---------------------------------------------------------------------------
# Minimal in-memory DB helper (for worker-layer tests that bypass the HTTP API)
# ---------------------------------------------------------------------------


def _init_minimal_db(db_path: str) -> None:
    """Create the event_actions table (and minimal dependencies) in a test DB.

    This replicates the schema from design §1.  The full init_db from db.py
    is preferred when testing through the HTTP API; this helper is for
    worker-layer unit tests that need fine-grained row control.

    TODO: replace with init_db(db_path) once the engineer adds event_actions
    to db.py's _SCHEMA — at that point this helper can be deleted.
    """
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workspaces (
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

        CREATE TABLE IF NOT EXISTS topics (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            subject TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            worktree_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE IF NOT EXISTS staffs (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('global','workspace','topic')),
            scope_id TEXT,
            name TEXT NOT NULL,
            adapter TEXT NOT NULL DEFAULT 'claude-code',
            model TEXT,
            system_prompt TEXT,
            agent TEXT,
            session_scope TEXT NOT NULL DEFAULT 'topic' CHECK (session_scope IN ('topic','workspace','global')),
            is_default INTEGER NOT NULL DEFAULT 0,
            extra_flags TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS staff_sessions (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL DEFAULT '',
            staff_name TEXT NOT NULL,
            session_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, staff_name)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            agent_name TEXT,
            text TEXT,
            transcript TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS config (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL DEFAULT '',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, key)
        );

        CREATE TABLE IF NOT EXISTS event_actions (
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
                (event_type = 'topic_scheduler'
                    AND cron_expr IS NOT NULL AND timing IS NULL)
                OR
                (event_type = 'topic_message_sent'
                    AND cron_expr IS NULL AND timing IN ('before','after'))
                OR
                (event_type IN ('topic_message_received','topic_archived')
                    AND cron_expr IS NULL AND (timing IS NULL OR timing = 'after'))
            )
        );

        CREATE INDEX IF NOT EXISTS idx_event_actions_scope_event
            ON event_actions (scope_type, scope_id, event_type, enabled);

        CREATE INDEX IF NOT EXISTS idx_event_actions_scheduler
            ON event_actions (event_type, enabled)
            WHERE event_type = 'topic_scheduler';
    """)
    conn.commit()
    conn.close()


def _insert_event_action(
    db_path: str,
    *,
    topic_id: str,
    event_type: str = "topic_message_sent",
    staff_name: str = "test-staff",
    prompt_template: str = "Hello {topic_name}",
    timing: str | None = "before",
    cron_expr: str | None = None,
    last_fired_at: str | None = None,
    enabled: int = 1,
) -> str:
    """Insert a row directly into event_actions; return the new id."""
    action_id = str(uuid.uuid4())
    now = _NOW_UTC
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO event_actions
            (id, event_type, scope_type, scope_id, staff_name, prompt_template,
             timing, cron_expr, last_fired_at, enabled, created_at, updated_at)
        VALUES (?, ?, 'topic', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (action_id, event_type, topic_id, staff_name, prompt_template,
         timing, cron_expr, last_fired_at, enabled, now, now),
    )
    conn.commit()
    conn.close()
    return action_id


def _insert_workspace_and_topic(
    db_path: str,
    *,
    workspace_name: str = "test-ws",
    topic_subject: str = "test topic",
    archived: bool = False,
) -> tuple[str, str]:
    """Insert minimal workspace + topic rows; return (workspace_id, topic_id)."""
    ws_id = str(uuid.uuid4())
    topic_id = str(uuid.uuid4())
    now = _NOW_UTC
    archived_at = now if archived else None
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO workspaces (id, name, repo_url, created_at) VALUES (?, ?, ?, ?)",
        (ws_id, workspace_name, "https://github.com/x/y", now),
    )
    conn.execute(
        "INSERT INTO topics (id, workspace_id, subject, branch_name, worktree_path, created_at, archived_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (topic_id, ws_id, topic_subject, "branch", "/tmp/wt", now, archived_at),
    )
    conn.commit()
    conn.close()
    return ws_id, topic_id


def _insert_staff(db_path: str, *, name: str, scope_type: str = "global",
                  scope_id: str | None = None) -> None:
    """Insert a minimal staff row."""
    conn = sqlite3.connect(db_path)
    now = _NOW_UTC
    conn.execute(
        "INSERT INTO staffs (id, scope_type, scope_id, name, adapter, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'claude-code', ?, ?)",
        (str(uuid.uuid4()), scope_type, scope_id, name, now, now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Area 1: Schema / CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    # TODO: fill body — depends on EventActionIn/EventActionOut and REST endpoints
    def test_list_empty(self, client, ea_base_url):
        # CRUD-01
        # TODO: fill body once REST endpoint paths are confirmed
        pytest.skip("scaffold only")

    def test_create_topic_message_sent_before(self, client, ea_base_url):
        # CRUD-02
        # TODO: fill body once EventActionIn/EventActionOut are stable
        pytest.skip("scaffold only")

    def test_create_topic_message_received(self, client, ea_base_url):
        # CRUD-03
        pytest.skip("scaffold only")

    def test_create_topic_scheduler(self, client, ea_base_url):
        # CRUD-04
        pytest.skip("scaffold only")

    def test_create_topic_archived(self, client, ea_base_url):
        # CRUD-05
        pytest.skip("scaffold only")

    def test_get_by_id(self, client, ea_base_url):
        # CRUD-06
        pytest.skip("scaffold only")

    def test_patch_enabled_false(self, client, ea_base_url):
        # CRUD-07
        pytest.skip("scaffold only")

    def test_patch_prompt_template(self, client, ea_base_url):
        # CRUD-08
        pytest.skip("scaffold only")

    def test_patch_readonly_fields_ignored_or_rejected(self, client, ea_base_url):
        # CRUD-09
        # TODO: fill body once engineer confirms whether read-only fields
        # on PATCH return 422 or are silently ignored (ambiguity flagged in test plan)
        pytest.skip("scaffold only — awaiting engineer clarification on PATCH read-only semantics")

    def test_delete(self, client, ea_base_url):
        # CRUD-10
        pytest.skip("scaffold only")

    def test_get_unknown_workspace(self, client, workspace_id):
        # CRUD-11
        pytest.skip("scaffold only")

    def test_get_unknown_action_id(self, client, ea_base_url):
        # CRUD-12
        pytest.skip("scaffold only")

    def test_create_workspace_scope_rejected(self, client, workspace_id, topic_id):
        # CRUD-13
        # POST with scope_type='workspace' should return 422 (not implemented in v1)
        pytest.skip("scaffold only")

    def test_freshly_created_run_fields_are_null(self, client, ea_base_url):
        # CRUD-14
        pytest.skip("scaffold only")

    def test_concurrent_patch_same_row(self, client, ea_base_url):
        # CRUD-15
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 2: Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_scheduler_missing_cron_expr(self, client, ea_base_url):
        # VAL-01
        pytest.skip("scaffold only")

    def test_scheduler_with_timing(self, client, ea_base_url):
        # VAL-02
        pytest.skip("scaffold only")

    def test_message_sent_missing_timing(self, client, ea_base_url):
        # VAL-03
        pytest.skip("scaffold only")

    def test_message_sent_with_cron_expr(self, client, ea_base_url):
        # VAL-04
        pytest.skip("scaffold only")

    def test_message_sent_timing_before_valid(self, client, ea_base_url):
        # VAL-05
        pytest.skip("scaffold only")

    def test_message_sent_timing_after_valid(self, client, ea_base_url):
        # VAL-06
        pytest.skip("scaffold only")

    def test_message_received_timing_before_rejected(self, client, ea_base_url):
        # VAL-07
        pytest.skip("scaffold only")

    def test_message_received_timing_after_valid(self, client, ea_base_url):
        # VAL-08
        # TODO: fill body — confirm that timing='after' on topic_message_received
        # returns 201 (ambiguity flagged in test plan)
        pytest.skip("scaffold only — awaiting engineer confirmation on timing='after' for message_received")

    def test_archived_timing_before_rejected(self, client, ea_base_url):
        # VAL-09
        pytest.skip("scaffold only")

    def test_archived_with_cron_expr_rejected(self, client, ea_base_url):
        # VAL-10
        pytest.skip("scaffold only")

    def test_invalid_cron_expr(self, client, ea_base_url):
        # VAL-11
        pytest.skip("scaffold only")

    def test_sub_minute_cron_rejected(self, client, ea_base_url):
        # VAL-12
        # TODO: fill body — confirm behaviour for 6-field cron expr
        # (ambiguity flagged in test plan)
        pytest.skip("scaffold only — awaiting engineer confirmation on sub-minute cron validation strategy")

    def test_valid_cron_0_9(self, client, ea_base_url):
        # VAL-13
        pytest.skip("scaffold only")

    def test_valid_cron_every_minute(self, client, ea_base_url):
        # VAL-14
        pytest.skip("scaffold only")

    def test_staff_name_not_validated_at_write(self, client, ea_base_url):
        # VAL-15
        pytest.skip("scaffold only")

    def test_template_unknown_placeholder_accepted(self, client, ea_base_url):
        # VAL-16
        pytest.skip("scaffold only")

    def test_invalid_event_type_rejected(self, client, ea_base_url):
        # VAL-17
        pytest.skip("scaffold only")

    def test_db_check_constraint_direct_insert(self, tmp_path):
        # VAL-18 — bypass the API and insert directly with a bad combo
        db_path = str(tmp_path / "check.db")
        _init_minimal_db(db_path)
        conn = sqlite3.connect(db_path)
        now = _NOW_UTC
        action_id = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO event_actions
                    (id, event_type, scope_type, scope_id, staff_name,
                     prompt_template, timing, cron_expr, enabled, created_at, updated_at)
                VALUES (?, 'topic_scheduler', 'topic', 'fake-topic', 'staff',
                        'hi', 'before', NULL, 1, ?, ?)
                """,
                (action_id, now, now),
            )
            conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Area 3: Variable Substitution
# ---------------------------------------------------------------------------


class TestTemplateRendering:
    """Pure-Python unit tests for render_template.

    TODO: import render_template once its module path is confirmed:
        from src.master.event_actions import render_template
    All tests in this class are TODO until that import path is known.
    """

    def test_known_variable_substituted(self):
        # TMPL-01
        # TODO: fill body once render_template is importable
        pytest.skip("scaffold only — depends on render_template module path")

    def test_all_message_sent_variables(self):
        # TMPL-02
        pytest.skip("scaffold only")

    def test_all_scheduler_variables(self):
        # TMPL-03
        pytest.skip("scaffold only")

    def test_all_archived_variables(self):
        # TMPL-04
        pytest.skip("scaffold only")

    def test_unknown_placeholder_kept_literal(self):
        # TMPL-05
        pytest.skip("scaffold only")

    def test_unknown_placeholder_logs_warning(self, caplog):
        # TMPL-06
        import logging
        pytest.skip("scaffold only")

    def test_double_brace_produces_literal_brace(self):
        # TMPL-07
        pytest.skip("scaffold only")

    def test_double_close_brace_produces_literal_brace(self):
        # TMPL-08
        pytest.skip("scaffold only")

    def test_no_placeholders_unchanged(self):
        # TMPL-09
        pytest.skip("scaffold only")

    def test_empty_template(self):
        # TMPL-10
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 4: Dispatch Core
# ---------------------------------------------------------------------------


class TestDispatchCore:
    """Tests for dispatch_to_staff extraction.

    TODO: import dispatch_to_staff once its module path is confirmed:
        from src.master.dispatch import dispatch_to_staff
    or
        from src.master.messages import dispatch_to_staff
    """

    def test_user_message_sender_is_user(self, client_mqtt):
        # DISP-01
        # TODO: fill body once dispatch_to_staff signature is settled
        pytest.skip("scaffold only — depends on dispatch_to_staff signature")

    def test_dispatch_returns_message_id(self, client_mqtt):
        # DISP-02
        pytest.skip("scaffold only")

    def test_dispatch_publishes_to_mqtt(self, client_mqtt):
        # DISP-03
        pytest.skip("scaffold only")

    def test_event_sender_inserts_event_message(self, client_mqtt):
        # DISP-04
        # TODO: fill body — also need to confirm messages.sender CHECK constraint
        # (ambiguity flagged in test plan)
        pytest.skip("scaffold only — awaiting engineer confirmation on messages.sender constraint")

    def test_session_uuid_matches_scope(self, client_mqtt):
        # DISP-05
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 5: Event Emission and Worker
# ---------------------------------------------------------------------------


class TestEmitAndWorker:
    """Tests for emit_event and event_worker.

    TODO: import once module path is confirmed:
        from src.master.event_actions import emit_event
        from src.master.event_worker import event_worker, _handle_event
    """

    def test_emit_from_loop_thread_enqueues(self, tmp_path):
        # EMIT-01
        # TODO: fill body once emit_event is importable
        pytest.skip("scaffold only — depends on emit_event module path")

    def test_emit_from_non_loop_thread_enqueues(self, tmp_path):
        # EMIT-02
        pytest.skip("scaffold only")

    def test_worker_dispatches_matching_actions(self, tmp_path):
        # EMIT-03
        # TODO: fill body once event_worker and dispatch_to_staff signatures are stable
        pytest.skip("scaffold only")

    def test_worker_fifo_order(self, tmp_path):
        # EMIT-04
        pytest.skip("scaffold only")

    def test_worker_processes_one_event_at_a_time(self, tmp_path):
        # EMIT-05
        pytest.skip("scaffold only")

    def test_worker_updates_last_progress(self, tmp_path):
        # EMIT-06
        pytest.skip("scaffold only")

    def test_disabled_action_not_dispatched(self, tmp_path, mqtt_recorder):
        # EMIT-07
        pytest.skip("scaffold only")

    def test_different_topic_action_not_fired(self, tmp_path, mqtt_recorder):
        # EMIT-08
        pytest.skip("scaffold only")

    def test_worker_error_isolation(self, tmp_path, caplog):
        # EMIT-09
        import logging
        pytest.skip("scaffold only")

    def test_worker_shutdown_drops_queue_cleanly(self, tmp_path):
        # EMIT-10
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 6: Multi-Action Fanout
# ---------------------------------------------------------------------------


class TestFanout:
    def test_two_actions_both_fire(self, tmp_path, mqtt_recorder):
        # FANOUT-01
        pytest.skip("scaffold only")

    def test_one_failure_does_not_block_sibling(self, tmp_path, mqtt_recorder):
        # FANOUT-02
        pytest.skip("scaffold only")

    def test_parallel_fanout_latency(self, tmp_path):
        # FANOUT-03
        # TODO: fill body — confirm timing strategy (monkeypatched sleep vs real
        # asyncio.sleep with loose bound) before writing assertion.
        # See ambiguity FANOUT-03 in test plan.
        pytest.skip("scaffold only — awaiting engineer confirmation on timing assertion strategy")

    def test_dispatch_timeout_records_error_sibling_unaffected(self, tmp_path, mqtt_recorder):
        # FANOUT-04
        pytest.skip("scaffold only")

    def test_worker_advances_after_timeout(self, tmp_path):
        # FANOUT-05
        pytest.skip("scaffold only")

    def test_gather_return_exceptions_catches_escape(self, tmp_path):
        # FANOUT-06
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 7: Loop Prevention
# ---------------------------------------------------------------------------


class TestLoopPrevention:
    def test_event_sender_does_not_retrigger_message_sent(self, tmp_path, client_mqtt):
        # LOOP-01
        # TODO: fill body once emit_event and dispatch_to_staff signatures are stable
        pytest.skip("scaffold only")

    def test_agent_reply_triggers_received_not_sent(self, tmp_path, client_mqtt):
        # LOOP-02
        pytest.skip("scaffold only")

    def test_archived_hook_fires_regardless_of_sender(self, tmp_path):
        # LOOP-03
        pytest.skip("scaffold only")

    def test_scheduler_hook_fires_regardless_of_sender(self, tmp_path):
        # LOOP-04
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 8: Per-Action Observability
# ---------------------------------------------------------------------------


class TestObservability:
    """Tests for last_run_at / last_run_status / last_run_output written by worker."""

    def test_ok_status_and_message_id_in_output(self, tmp_path):
        # OBS-01
        pytest.skip("scaffold only")

    def test_staff_missing_status(self, tmp_path):
        # OBS-02
        pytest.skip("scaffold only")

    def test_render_error_status(self, tmp_path):
        # OBS-03
        pytest.skip("scaffold only")

    def test_dispatch_timeout_status(self, tmp_path):
        # OBS-04
        pytest.skip("scaffold only")

    def test_dispatch_exception_status(self, tmp_path):
        # OBS-05
        pytest.skip("scaffold only")

    def test_last_run_at_updated_all_outcomes(self, tmp_path):
        # OBS-06
        pytest.skip("scaffold only")

    def test_last_fired_at_unchanged_for_non_scheduler(self, tmp_path):
        # OBS-07
        pytest.skip("scaffold only")

    def test_last_fired_at_vs_last_run_at_scheduler(self, tmp_path):
        # OBS-08
        pytest.skip("scaffold only")

    def test_last_run_output_truncated_at_4096(self, tmp_path):
        # OBS-09
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 9: Scheduler
# ---------------------------------------------------------------------------


class TestScheduler:
    """Tests for _scheduler_tick.

    Uses real asyncio.Queue + frozen clock.  No real clock sleeping.
    TODO: import _scheduler_tick once its module path is confirmed.
    """

    def test_cron_tz_evaluation_shanghai(self, tmp_path, frozen_utc):
        # SCHED-01: 0 9 * * * in Asia/Shanghai fires at 01:00 UTC
        # TODO: fill body once _scheduler_tick is importable and
        # get_configured_timezone interface is known
        pytest.skip("scaffold only — depends on _scheduler_tick and get_configured_timezone")

    def test_action_due_65s_ago(self, tmp_path, frozen_utc_65s_ago):
        # SCHED-02
        pytest.skip("scaffold only")

    def test_action_not_due_5s_ago(self, tmp_path, frozen_utc_5s_ago):
        # SCHED-03
        pytest.skip("scaffold only")

    def test_archived_topic_not_fired(self, tmp_path, frozen_utc):
        # SCHED-04
        db_path = str(tmp_path / "db.db")
        _init_minimal_db(db_path)
        ws_id, topic_id = _insert_workspace_and_topic(db_path, archived=True)
        # Insert scheduler action for the archived topic
        _insert_event_action(
            db_path,
            topic_id=topic_id,
            event_type="topic_scheduler",
            cron_expr=_CRON_EVERY_MINUTE,
            timing=None,
            last_fired_at=None,
        )
        # TODO: fill assertion once _scheduler_tick is importable
        pytest.skip("scaffold only — depends on _scheduler_tick")

    def test_watermark_advances_to_next_fire_not_now(self, tmp_path, frozen_utc):
        # SCHED-05
        pytest.skip("scaffold only")

    def test_slow_worker_no_duplicate_fire(self, tmp_path, frozen_utc):
        # SCHED-06
        pytest.skip("scaffold only")

    def test_last_fired_at_stored_as_utc_iso8601(self, tmp_path, frozen_utc):
        # SCHED-07
        pytest.skip("scaffold only")

    def test_null_last_fired_at_uses_created_at(self, tmp_path, frozen_utc):
        # SCHED-08
        pytest.skip("scaffold only")

    def test_catchup_fires_once_per_tick(self, tmp_path, frozen_utc):
        # SCHED-09
        pytest.skip("scaffold only")

    def test_configured_tz_changes_next_fire(self, tmp_path, frozen_utc):
        # SCHED-10
        pytest.skip("scaffold only")

    def test_scheduler_slot_and_action_id_in_event(self, tmp_path, frozen_utc):
        # SCHED-11
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 10: Stall Watchdog
# ---------------------------------------------------------------------------


class TestStallWatchdog:
    """Tests for _worker_watchdog.

    WATCH-01 / WATCH-02 are timing-sensitive.  The watchdog sleeps 30 s
    between polls; the sleep must be monkeypatched in tests (see ambiguity
    WATCH-01 in test plan).
    TODO: import _worker_watchdog once module path is confirmed.
    """

    def test_watchdog_logs_warn_when_worker_blocked_queue_nonempty(self, caplog):
        # WATCH-01
        import logging
        # TODO: fill body — depends on _worker_watchdog being importable and
        # sleep interval being injectable (ambiguity flagged in test plan)
        pytest.skip("scaffold only — depends on _worker_watchdog and injectable sleep interval")

    def test_watchdog_no_log_when_queue_empty(self, caplog):
        # WATCH-02
        import logging
        pytest.skip("scaffold only")

    def test_watchdog_does_not_cancel_worker(self):
        # WATCH-03
        pytest.skip("scaffold only")


# ---------------------------------------------------------------------------
# Area 11: Session Sharing
# ---------------------------------------------------------------------------


class TestSessionSharing:
    def test_event_dispatch_shares_session_with_user_dispatch(self, tmp_path, client_mqtt):
        # SESS-01
        # TODO: fill body once dispatch_to_staff is extractable and
        # _staff_session_key interface is confirmed
        pytest.skip("scaffold only — depends on dispatch_to_staff signature")

    def test_two_staffs_use_different_sessions(self, tmp_path, client_mqtt):
        # SESS-02
        pytest.skip("scaffold only")

    def test_workspace_scope_shares_across_topics(self, tmp_path, client_mqtt):
        # SESS-03
        pytest.skip("scaffold only")
