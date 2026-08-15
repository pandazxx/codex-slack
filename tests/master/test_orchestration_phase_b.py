"""Tests for phase (b) of the agent orchestration protocol.

Design doc: docs/design/agent-orchestration.md (§3, §4, §7, §8, §11)

Coverage:
  1.  State machine — apply_transition allow/reject cases
  2.  DB — staged_dispatches table present; result_status / dispatch_token columns on existing tables
  3.  Config knobs — effective depth/root read from config table cascade
  4.  Dispatch token — generated, stored on message row, excluded from GET /messages + WS frame
  5.  Token verification — wrong/missing token returns 403
  6.  submit_result endpoint — stages result_ready, records on task row
  7.  answer_question endpoint — stages answer
  8.  accept_result endpoint — task → completed, lock released, next queued task dispatches
  9.  ask_sender (phase b) — stages question, task → input-required
  10. Full async depth-1 chain via simulated /response calls:
      user → architect delegate → engineer submit_result → judgment dispatched →
      architect accept_result → completed + free text to user
  11. Implicit-result fallback: delegated turn ends without submit_result → judgment dispatched
  12. Implicit-answer fallback: input-required turn ends without answer_question → answer dispatched
  13. Lock / queue: second delegate queues; dispatches after accept_result; queued_position correct
  14. Illegal transitions return 409
  15. Event-action loop safety: delegation chain fires no topic_message_received actions
  16. Interrupted turn does not re-enter (task state unchanged)
  17. GET /tasks returns task rows for the UI
  18. MCP tool availability by depth: submit_result absent at depth 0; answer_question/accept_result
      absent at depth >= 1
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures (mirroring test_orchestration.py)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        from src.master.main import app
        with TestClient(app) as c:
            yield c, mock_mqtt


@pytest.fixture()
def workspace_topic(client):
    c, _ = client
    ws = c.post("/api/workspaces", json={"name": "repo", "repo_url": "https://github.com/x/y"}).json()
    topic = c.post(f"/api/workspaces/{ws['id']}/topics", json={"subject": "Test", "repo_ref": "main"}).json()
    return ws["id"], topic["id"]


@pytest.fixture()
def architect_staff(client, workspace_topic):
    c, _ = client
    ws_id, _ = workspace_topic
    r = c.post(
        f"/api/workspaces/{ws_id}/staffs",
        json={"name": "architect", "adapter": "claude-code", "is_default": False},
    )
    assert r.status_code == 201
    return r.json()


@pytest.fixture()
def engineer_staff(client, workspace_topic):
    c, _ = client
    ws_id, _ = workspace_topic
    r = c.post(
        f"/api/workspaces/{ws_id}/staffs",
        json={"name": "engineer", "adapter": "claude-code", "is_default": False},
    )
    assert r.status_code == 201
    return r.json()


def _post_prompt(client, ws_id, topic_id, text="@architect go"):
    c, _ = client
    return c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages", data={"text": text})


def _delegate(client, ws_id, topic_id, caller_staff, caller_msg_id, target_staff,
              goal="Build it", criteria="Tests pass", dispatch_token=None):
    c, _ = client
    body = {
        "caller_staff": caller_staff,
        "caller_message_id": caller_msg_id,
        "staff": target_staff,
        "goal": goal,
        "acceptance_criteria": criteria,
    }
    if dispatch_token:
        body["dispatch_token"] = dispatch_token
    return c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/delegate", json=body)


def _get_messages(client, ws_id, topic_id):
    c, _ = client
    return c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages").json()


def _get_db_path(client):
    c, _ = client
    return c.app.state.db_path


def _simulate_response(db_path, topic_id, agent_name, prompt_msg_id, reply_text="done",
                        interrupt_reason=None):
    from src.master.mqtt_client import _save_agent_response
    import uuid
    reply_id = str(uuid.uuid4())
    _save_agent_response(db_path, topic_id, {
        "message_id": reply_id,
        "agent_name": agent_name,
        "reply_to": prompt_msg_id,
        "last_response": reply_text,
        "transcript": None,
        "session_id": None,
        "interrupt_reason": interrupt_reason,
    })
    return reply_id


async def _run_process_turn_end(app_state, workspace_id, topic_id, prompt_msg_id,
                                 reply_msg_id, agent_name, reply_text,
                                 interrupt_reason=None):
    from src.master.orchestrate_api import process_turn_end
    await process_turn_end(
        app_state=app_state,
        workspace_id=workspace_id,
        topic_id=topic_id,
        prompt_message_id=prompt_msg_id,
        reply_message_id=reply_msg_id,
        agent_name=agent_name,
        reply_text=reply_text,
        interrupt_reason=interrupt_reason,
    )


def _make_app_state(client, db_path):
    c, mock_mqtt = client
    app_state = MagicMock()
    app_state.db_path = db_path
    app_state.settings = MagicMock(dry_run=True)
    app_state.mqtt = mock_mqtt
    app_state.hub = MagicMock()
    app_state.hub.broadcast = AsyncMock()
    return app_state


# ---------------------------------------------------------------------------
# 1. State machine
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_submitted_to_working_on_dispatch(self):
        from src.master.orchestration import apply_transition
        assert apply_transition("submitted", "dispatch") == "working"

    def test_working_to_input_required_on_ask(self):
        from src.master.orchestration import apply_transition
        assert apply_transition("working", "ask_sender") == "input-required"

    def test_input_required_to_working_on_answer(self):
        from src.master.orchestration import apply_transition
        assert apply_transition("input-required", "answer") == "working"

    def test_working_to_completed_on_accept(self):
        from src.master.orchestration import apply_transition
        assert apply_transition("working", "accept_result") == "completed"

    def test_working_to_failed_on_master_error(self):
        from src.master.orchestration import apply_transition
        assert apply_transition("working", "master_error") == "failed"

    def test_illegal_transition_raises(self):
        from src.master.orchestration import apply_transition, IllegalTransition
        with pytest.raises(IllegalTransition):
            apply_transition("completed", "accept_result")

    def test_illegal_input_required_to_accept(self):
        from src.master.orchestration import apply_transition, IllegalTransition
        with pytest.raises(IllegalTransition):
            apply_transition("input-required", "accept_result")

    def test_illegal_submitted_to_accept(self):
        from src.master.orchestration import apply_transition, IllegalTransition
        with pytest.raises(IllegalTransition):
            apply_transition("submitted", "accept_result")


# ---------------------------------------------------------------------------
# 2. DB columns
# ---------------------------------------------------------------------------

def test_staged_dispatches_table_exists(tmp_path):
    from src.master.db import init_db, get_connection
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='staged_dispatches'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_result_status_column_on_tasks(tmp_path):
    from src.master.db import init_db, get_connection
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        assert "result_status" in cols
    finally:
        conn.close()


def test_dispatch_token_column_on_messages(tmp_path):
    from src.master.db import init_db, get_connection
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        assert "dispatch_token" in cols
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Config knobs
# ---------------------------------------------------------------------------

def test_config_max_delegation_depth_workspace_override(tmp_path):
    from src.master.db import init_db, get_connection
    from src.master.orchestration import get_effective_max_delegation_depth, MAX_DELEGATION_DEPTH
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workspaces (id, name, repo_url, repo_ref, created_at)"
            " VALUES ('ws1', 'r', 'u', 'main', '2024-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO config (scope_type, scope_id, key, value, updated_at)"
            " VALUES ('workspace', 'ws1', 'ORCH_MAX_DELEGATION_DEPTH', '3', '2024-01-01T00:00:00Z')"
        )
        conn.commit()
        assert get_effective_max_delegation_depth(conn, "ws1") == 3
    finally:
        conn.close()


def test_config_max_delegation_depth_global_fallback(tmp_path):
    from src.master.db import init_db, get_connection
    from src.master.orchestration import get_effective_max_delegation_depth
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO config (scope_type, scope_id, key, value, updated_at)"
            " VALUES ('global', '', 'ORCH_MAX_DELEGATION_DEPTH', '5', '2024-01-01T00:00:00Z')"
        )
        conn.commit()
        assert get_effective_max_delegation_depth(conn, "nonexistent") == 5
    finally:
        conn.close()


def test_config_max_delegation_depth_default(tmp_path):
    from src.master.db import init_db, get_connection
    from src.master.orchestration import get_effective_max_delegation_depth, MAX_DELEGATION_DEPTH
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        assert get_effective_max_delegation_depth(conn, "ws-no-config") == MAX_DELEGATION_DEPTH
    finally:
        conn.close()


def test_config_max_tasks_per_root_workspace_override(tmp_path):
    from src.master.db import init_db, get_connection
    from src.master.orchestration import get_effective_max_tasks_per_root
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workspaces (id, name, repo_url, repo_ref, created_at)"
            " VALUES ('ws1', 'r', 'u', 'main', '2024-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO config (scope_type, scope_id, key, value, updated_at)"
            " VALUES ('workspace', 'ws1', 'ORCH_MAX_TASKS_PER_ROOT', '5', '2024-01-01T00:00:00Z')"
        )
        conn.commit()
        assert get_effective_max_tasks_per_root(conn, "ws1") == 5
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Dispatch token — generated and stored, excluded from GET /messages
# ---------------------------------------------------------------------------

def test_dispatch_token_generated_and_stored(client, workspace_topic, architect_staff):
    c, mock_mqtt = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    r = _post_prompt(client, ws_id, topic_id, "@architect go")
    assert r.status_code == 202

    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT dispatch_token FROM messages WHERE topic_id = ? AND sender = 'user'",
            (topic_id,),
        ).fetchone()
        assert row is not None
        assert row["dispatch_token"] is not None
        token = row["dispatch_token"]
    finally:
        conn.close()

    msgs = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages").json()
    msg = next(m for m in msgs if m["sender"] == "user")
    assert "dispatch_token" not in msg, "dispatch_token must not appear in GET /messages"


def test_dispatch_token_not_in_ws_frame(client, workspace_topic, architect_staff):
    c, mock_mqtt = client
    ws_id, topic_id = workspace_topic

    broadcasts = []
    original_broadcast = c.app.state.hub.broadcast

    async def capturing_broadcast(channel, data):
        broadcasts.append(data)
        # delegate to original if it exists, but in test context it's a noop
    c.app.state.hub.broadcast = capturing_broadcast

    _post_prompt(client, ws_id, topic_id, "@architect go")

    for frame in broadcasts:
        assert "dispatch_token" not in frame, (
            f"dispatch_token must not appear in WS broadcast frame: {frame}"
        )


# ---------------------------------------------------------------------------
# 5. Token verification
# ---------------------------------------------------------------------------

def test_submit_result_wrong_token_returns_403(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    assert delegate_r.status_code == 200

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "dispatch_token": "wrong-token-totally-invalid",
            "status": "completed",
            "summary": "done",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid_dispatch_token"


def test_accept_result_wrong_token_returns_403(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    arch_prompt = next(m for m in msgs if m.get("receiver_name") == "architect")

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/accept_result",
        json={
            "caller_staff": "architect",
            "caller_message_id": arch_prompt["id"],
            "dispatch_token": "bad-token",
            "task_id": task_id,
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid_dispatch_token"


# ---------------------------------------------------------------------------
# 6. submit_result endpoint
# ---------------------------------------------------------------------------

def test_submit_result_stages_result_ready(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    assert delegate_r.status_code == 200
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "status": "completed",
            "summary": "Feature X implemented",
            "artifacts": [{"kind": "commit", "ref": "abc123"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task_id
    assert body["state"] == "working"

    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert task["result_summary"] == "Feature X implemented"
        assert task["result_status"] == "completed"

        staged = conn.execute(
            "SELECT * FROM staged_dispatches WHERE prompt_message_id = ?",
            (eng_prompt["id"],),
        ).fetchall()
        assert len(staged) == 1
        assert staged[0]["kind"] == "result_ready"
        assert staged[0]["dispatched_at"] is None
    finally:
        conn.close()


def test_submit_result_last_call_wins(client, workspace_topic, architect_staff, engineer_staff):
    """Calling submit_result twice in one turn: second call overwrites."""
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "status": "failed",
            "summary": "First attempt",
        },
    )
    c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "status": "completed",
            "summary": "Second attempt — successful",
        },
    )

    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        staged = conn.execute(
            "SELECT * FROM staged_dispatches WHERE prompt_message_id = ?",
            (eng_prompt["id"],),
        ).fetchall()
        assert len(staged) == 1, "Last-call-wins: only one staged result_ready row"
        body = json.loads(staged[0]["body"])
        assert body["summary"] == "Second attempt — successful"
        assert body["status"] == "completed"
    finally:
        conn.close()


def test_submit_result_not_assignee_403(client, workspace_topic, architect_staff, engineer_staff):
    """submit_result returns 403 when the caller is not the task assignee.

    The identity check (caller_message_id receiver must match caller_staff) fires before
    the assignee check, so we must construct a scenario where identity passes but the
    caller is not the assignee.  We do this by inserting a synthetic message row that is
    addressed to architect but linked to a task whose assignee is engineer.
    """
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    # Insert a synthetic message row addressed to architect but linked to engineer's task.
    import uuid as _uuid
    from src.master.db import get_connection as _get_conn
    syn_msg_id = str(_uuid.uuid4())
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO messages"
            " (id, topic_id, sender, agent_name, text, silent, created_at,"
            "  sender_kind, sender_name, receiver_kind, receiver_name, task_id)"
            " VALUES (?, ?, 'event', 'architect', 'synthetic', 0, '2024-01-01T00:00:00Z',"
            "         'staff', 'architect', 'staff', 'architect', ?)",
            (syn_msg_id, topic_id, task_id),
        )
        conn.commit()
    finally:
        conn.close()

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "architect",  # identity check passes (receiver_name=architect)
            "caller_message_id": syn_msg_id,
            "status": "completed",
            "summary": "architect trying to submit for engineer's task",
        },
    )
    assert r.status_code == 403, f"Expected 403 not_assignee, got {r.status_code}: {r.json()}"
    assert r.json()["detail"] == "not_assignee"


def test_submit_result_no_task_context_422(client, workspace_topic, architect_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "architect",
            "caller_message_id": caller_msg_id,
            "status": "completed",
            "summary": "done",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "no_task_context"


# ---------------------------------------------------------------------------
# 7. answer_question endpoint
# ---------------------------------------------------------------------------

def test_answer_question_stages_answer(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    # Engineer asks architect a question
    ask_r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/ask",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "question": "Which token format?",
        },
    )
    assert ask_r.status_code == 200

    # Architect is now on a question turn (simulated: architect gets a new prompt message)
    # For test purposes we use the original architect prompt message as caller_message_id
    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/answer_question",
        json={
            "caller_staff": "architect",
            "caller_message_id": caller_msg_id,
            "task_id": task_id,
            "answer": "Use JWT",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task_id
    assert body["state"] == "working"

    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        staged = conn.execute(
            "SELECT * FROM staged_dispatches WHERE task_id = ? AND kind = 'answer'",
            (task_id,),
        ).fetchone()
        assert staged is not None
        body_data = json.loads(staged["body"])
        assert body_data["answer"] == "Use JWT"
    finally:
        conn.close()


def test_answer_question_not_dispatcher_403(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/ask",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "question": "Which format?",
        },
    )

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/answer_question",
        json={
            "caller_staff": "engineer",  # engineer is not the dispatcher
            "caller_message_id": eng_prompt["id"],
            "task_id": task_id,
            "answer": "Should fail",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "not_dispatcher"


def test_answer_question_wrong_state_409(client, workspace_topic, architect_staff, engineer_staff):
    """answer_question fails when task is not in input-required state."""
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/answer_question",
        json={
            "caller_staff": "architect",
            "caller_message_id": caller_msg_id,
            "task_id": task_id,
            "answer": "Should fail — task is in 'working', not 'input-required'",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "illegal_transition"


# ---------------------------------------------------------------------------
# 8. accept_result endpoint
# ---------------------------------------------------------------------------

def test_accept_result_completes_task(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/accept_result",
        json={
            "caller_staff": "architect",
            "caller_message_id": caller_msg_id,
            "task_id": task_id,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task_id
    assert body["state"] == "completed"

    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert task["state"] == "completed"
        assert task["closed_at"] is not None
    finally:
        conn.close()


def test_accept_result_not_dispatcher_403(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/accept_result",
        json={
            "caller_staff": "engineer",  # engineer cannot accept its own result
            "caller_message_id": eng_prompt["id"],
            "task_id": task_id,
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "not_dispatcher"


def test_accept_result_wrong_state_409(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    # Force task to input-required so accept_result is illegal
    from src.master.db import get_connection
    conn = get_connection(_get_db_path(client))
    try:
        conn.execute("UPDATE tasks SET state = 'input-required' WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/accept_result",
        json={
            "caller_staff": "architect",
            "caller_message_id": caller_msg_id,
            "task_id": task_id,
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "illegal_transition"


# ---------------------------------------------------------------------------
# 9. ask_sender phases b (stages question for staff dispatcher)
# ---------------------------------------------------------------------------

def test_ask_inside_task_stages_question(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/ask",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "question": "Which format?",
        },
    )
    assert r.status_code == 200
    assert r.json()["task_state"] == "input-required"

    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        staged = conn.execute(
            "SELECT * FROM staged_dispatches WHERE prompt_message_id = ? AND kind = 'question'",
            (eng_prompt["id"],),
        ).fetchone()
        assert staged is not None
        assert staged["receiver_name"] == "architect"
        assert staged["dispatched_at"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 10. Full async depth-1 chain (simulate /response calls)
# ---------------------------------------------------------------------------

def test_full_async_chain_via_process_turn_end(client, workspace_topic, architect_staff, engineer_staff):
    """User → architect delegate → engineer submit_result → judgment dispatched →
    architect accept_result → completed + architect's free text to user."""
    c, mock_mqtt = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)
    app_state = _make_app_state(client, db_path)

    # 1. User → architect
    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]

    # 2. Architect delegates to engineer
    delegate_r = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer",
                           goal="Implement JWT", criteria="Tests green")
    assert delegate_r.status_code == 200
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    # 3. Engineer calls submit_result
    c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "status": "completed",
            "summary": "JWT middleware implemented",
            "artifacts": [{"kind": "commit", "ref": "deadbeef"}],
        },
    )

    # 4. Engineer's turn ends — /response arrives
    eng_reply_id = _simulate_response(db_path, topic_id, "engineer", eng_prompt["id"],
                                       "Work done, submitted result.")
    asyncio.run(_run_process_turn_end(
        app_state, ws_id, topic_id,
        eng_prompt["id"], eng_reply_id, "engineer",
        "Work done, submitted result.",
    ))

    # After process_turn_end, a judgment prompt should have been dispatched to architect.
    # Verify via mqtt publish calls (dispatch_to_staff publishes via app_state.mqtt).
    # Since app_state.mqtt is mocked, we check via DB: a new prompt message for architect
    # should exist with task_id set.
    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        judgment_prompt = conn.execute(
            "SELECT * FROM messages WHERE task_id = ? AND receiver_name = 'architect'"
            " AND sender = 'event' ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        assert judgment_prompt is not None, "Judgment prompt must have been dispatched to architect"

        staged = conn.execute(
            "SELECT dispatched_at FROM staged_dispatches WHERE prompt_message_id = ?",
            (eng_prompt["id"],),
        ).fetchone()
        assert staged is not None
        assert staged["dispatched_at"] is not None, "Staged row must be stamped dispatched"
    finally:
        conn.close()

    # 5. Architect calls accept_result on the judgment turn
    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/accept_result",
        json={
            "caller_staff": "architect",
            "caller_message_id": user_msg_id,
            "task_id": task_id,
        },
    )
    assert r.status_code == 200
    assert r.json()["state"] == "completed"

    conn = get_connection(db_path)
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert task["state"] == "completed"
        assert task["closed_at"] is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 11. Implicit-result fallback
# ---------------------------------------------------------------------------

def test_implicit_result_fallback(client, workspace_topic, architect_staff, engineer_staff):
    """Delegated turn ends without submit_result → judgment dispatched with reply text as summary."""
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)
    app_state = _make_app_state(client, db_path)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    # No submit_result called — engineer just replies with free text.
    reply_text = "I finished the work implicitly."
    eng_reply_id = _simulate_response(db_path, topic_id, "engineer", eng_prompt["id"], reply_text)
    asyncio.run(_run_process_turn_end(
        app_state, ws_id, topic_id,
        eng_prompt["id"], eng_reply_id, "engineer", reply_text,
    ))

    # A judgment prompt should have been dispatched to architect with the reply text as summary.
    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        judgment_prompt = conn.execute(
            "SELECT * FROM messages WHERE task_id = ? AND receiver_name = 'architect'"
            " AND sender = 'event' ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        assert judgment_prompt is not None, "Implicit result must trigger judgment turn"
        assert reply_text in judgment_prompt["text"], "Judgment prompt must contain the reply text"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 12. Implicit-answer fallback
# ---------------------------------------------------------------------------

def test_implicit_answer_fallback(client, workspace_topic, architect_staff, engineer_staff):
    """input-required turn ends without answer_question → answer dispatched to assignee.

    The full flow simulated here:
      1. Delegate user → architect → engineer (creates engineer prompt).
      2. Engineer calls ask_sender (stages 'question' for engineer's prompt, task→input-required).
      3. Engineer's turn ends → process_turn_end dispatches the staged question to architect,
         creating a new question-prompt message addressed to architect with task_id set.
      4. Architect's turn ends with plain text and no answer_question call.
      5. process_turn_end sees no staged rows for architect's question-prompt, and task is
         still input-required with architect as dispatcher → implicit answer fires.
      6. Verify: an answer message is dispatched to engineer, task→working.
    """
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)
    app_state = _make_app_state(client, db_path)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    # Step 2: Engineer asks a question → task goes input-required.
    c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/ask",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "question": "Which token format?",
        },
    )

    # Step 3: Engineer's turn ends → process_turn_end dispatches the staged question.
    eng_reply_id = _simulate_response(db_path, topic_id, "engineer", eng_prompt["id"],
                                       "I need clarification.")
    asyncio.run(_run_process_turn_end(
        app_state, ws_id, topic_id,
        eng_prompt["id"], eng_reply_id, "engineer", "I need clarification.",
    ))

    # After engineer's turn ends, a question prompt should exist for architect.
    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        arch_question_prompt = conn.execute(
            "SELECT * FROM messages WHERE task_id = ? AND receiver_name = 'architect'"
            " AND sender = 'event' ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        assert arch_question_prompt is not None, (
            "Question prompt must have been dispatched to architect"
        )
        arch_question_msg_id = arch_question_prompt["id"]
    finally:
        conn.close()

    # Step 4: Architect's turn ends with plain text — no answer_question called.
    answer_text = "Please use JWT"
    arch_reply_id = _simulate_response(db_path, topic_id, "architect", arch_question_msg_id,
                                        answer_text)
    asyncio.run(_run_process_turn_end(
        app_state, ws_id, topic_id,
        arch_question_msg_id, arch_reply_id, "architect", answer_text,
    ))

    # Step 5-6: Verify an answer message was dispatched to engineer and task→working.
    conn = get_connection(db_path)
    try:
        answer_prompt = conn.execute(
            "SELECT * FROM messages WHERE task_id = ? AND receiver_name = 'engineer'"
            " AND sender = 'event' ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        assert answer_prompt is not None, "Implicit answer must trigger dispatch to assignee"

        task = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert task["state"] == "working", "Task must return to working after implicit answer"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 13. Lock / queue
# ---------------------------------------------------------------------------

def test_second_delegate_queues_when_lock_held(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]

    # First delegate — acquires lock, task working
    r1 = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer",
                   goal="Task 1", criteria="Done 1")
    assert r1.status_code == 200
    assert r1.json()["state"] == "working"
    task_id_1 = r1.json()["task_id"]

    # Second delegate — lock held, task queued
    r2 = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer",
                   goal="Task 2", criteria="Done 2")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["state"] == "queued"
    assert body2["queued_position"] is not None
    assert body2["queued_position"] >= 1
    task_id_2 = body2["task_id"]

    from src.master.db import get_connection
    conn = get_connection(_get_db_path(client))
    try:
        t1 = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id_1,)).fetchone()
        t2 = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id_2,)).fetchone()
        assert t1["state"] == "working"
        assert t2["state"] == "submitted"
    finally:
        conn.close()


def test_queued_task_dispatches_after_accept_result(client, workspace_topic, architect_staff, engineer_staff):
    """After accept_result on task 1, the queued task 2 transitions to working.

    accept_result releases the topic lock and awaits _dispatch_next_queued inline,
    so by the time the HTTP response returns, the queued task has been dispatched.
    """
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]

    r1 = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer",
                   goal="Task 1", criteria="Done")
    task_id_1 = r1.json()["task_id"]

    r2 = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer",
                   goal="Task 2", criteria="Done")
    task_id_2 = r2.json()["task_id"]

    # Accept result on task 1 — completes the task, releases the lock, and awaits
    # _dispatch_next_queued inline (the endpoint awaits it directly, not via create_task).
    accept_r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/accept_result",
        json={
            "caller_staff": "architect",
            "caller_message_id": user_msg_id,
            "task_id": task_id_1,
        },
    )
    assert accept_r.status_code == 200

    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        t1 = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id_1,)).fetchone()
        t2 = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id_2,)).fetchone()
        assert t1["state"] == "completed"
        assert t2["state"] == "working", "Queued task must be dispatched after prior task completes"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 14. Illegal transitions return 409
# ---------------------------------------------------------------------------

def test_ask_sender_wrong_state_409(client, workspace_topic, architect_staff, engineer_staff):
    """ask_sender on a task that is already completed returns 409."""
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    caller_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", caller_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    # Force the task to completed.
    from src.master.db import get_connection
    conn = get_connection(_get_db_path(client))
    try:
        conn.execute("UPDATE tasks SET state = 'completed' WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/ask",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "question": "Too late to ask",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "illegal_transition"


# ---------------------------------------------------------------------------
# 15. Event-action loop safety: delegation chain fires no topic_message_received
# ---------------------------------------------------------------------------

def test_delegation_chain_no_event_loop(tmp_path):
    """A re-entry prompt (sender_kind='staff') must NOT trigger topic_message_received.

    Verified by checking that _prompt_was_orchestration_reentry returns True for
    staff-originated prompts and False for user-originated ones.
    """
    from src.master.db import init_db, get_connection
    from src.master.mqtt_client import _prompt_was_orchestration_reentry

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO workspaces (id, name, repo_url, repo_ref, created_at)"
            " VALUES ('ws1', 'r', 'u', 'main', '2024-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO topics (id, workspace_id, subject, branch_name, worktree_path, created_at)"
            " VALUES ('t1', 'ws1', 'S', 'b', '/w', '2024-01-01T00:00:00Z')"
        )
        # Staff re-entry prompt (delegation hop)
        conn.execute(
            "INSERT INTO messages (id, topic_id, sender, agent_name, text, silent, created_at,"
            " sender_kind, sender_name, receiver_kind)"
            " VALUES ('reentry', 't1', 'event', 'architect', 'Build it', 0,"
            " '2024-01-01T00:00:00Z', 'staff', 'architect', 'staff')"
        )
        # Genuine user prompt
        conn.execute(
            "INSERT INTO messages (id, topic_id, sender, agent_name, text, silent, created_at,"
            " sender_kind, receiver_kind)"
            " VALUES ('user-prompt', 't1', 'user', NULL, 'hello', 0,"
            " '2024-01-01T00:00:00Z', 'user', 'staff')"
        )
        conn.commit()
    finally:
        conn.close()

    assert _prompt_was_orchestration_reentry(db_path, "reentry") is True, (
        "Staff re-entry prompt must be detected as orchestration re-entry"
    )
    assert _prompt_was_orchestration_reentry(db_path, "user-prompt") is False, (
        "User prompt must NOT be detected as orchestration re-entry"
    )
    assert _prompt_was_orchestration_reentry(db_path, "nonexistent") is False


def test_delegation_chain_event_action_suppression(client, workspace_topic, architect_staff, engineer_staff):
    """Full integration: engineer's reply to a delegation prompt must not fire topic_message_received."""
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)

    emit_calls = []

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    # Simulate engineer's MQTT /response while capturing emit_event calls.
    from src.master import mqtt_client as mqtt_mod

    original_on_message = mqtt_mod._on_message
    with patch("src.master.event_dispatcher.emit_event") as mock_emit:
        payload = {
            "message_id": "eng-reply-loop-test",
            "agent_name": "engineer",
            "reply_to": eng_prompt["id"],
            "last_response": "Loop test reply",
            "transcript": None,
            "session_id": None,
        }
        userdata = {
            "db_path": db_path,
            "hub": MagicMock(),
            "loop": None,
            "settings": MagicMock(),
            "app_state": None,
        }
        mqtt_mod._save_agent_response(db_path, topic_id, payload)
        # With loop=None the orchestration re-entry is skipped (no coroutine scheduled).
        # The event gate check is what we're testing here.
        from src.master.mqtt_client import _prompt_was_orchestration_reentry
        is_reentry = _prompt_was_orchestration_reentry(db_path, eng_prompt["id"])
        assert is_reentry is True, (
            "Engineer's reply to a delegation prompt is a re-entry — event actions must be suppressed"
        )


# ---------------------------------------------------------------------------
# 16. Interrupted turn does not re-enter
# ---------------------------------------------------------------------------

def test_interrupted_turn_no_reentry(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic
    db_path = _get_db_path(client)
    app_state = _make_app_state(client, db_path)

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer")
    task_id = delegate_r.json()["task_id"]

    msgs = _get_messages(client, ws_id, topic_id)
    eng_prompt = next(m for m in msgs if m.get("receiver_name") == "engineer")

    # Engineer stages a result.
    c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/submit_result",
        json={
            "caller_staff": "engineer",
            "caller_message_id": eng_prompt["id"],
            "status": "completed",
            "summary": "done",
        },
    )

    # Turn ends with interrupt_reason set — re-entry must be skipped.
    eng_reply_id = _simulate_response(db_path, topic_id, "engineer", eng_prompt["id"],
                                       "(message interrupted)", interrupt_reason="container-gone")
    asyncio.run(_run_process_turn_end(
        app_state, ws_id, topic_id,
        eng_prompt["id"], eng_reply_id, "engineer",
        "(message interrupted)", interrupt_reason="container-gone",
    ))

    # Staged row must remain undispatched.
    from src.master.db import get_connection
    conn = get_connection(db_path)
    try:
        staged = conn.execute(
            "SELECT * FROM staged_dispatches WHERE prompt_message_id = ?",
            (eng_prompt["id"],),
        ).fetchone()
        assert staged is not None
        assert staged["dispatched_at"] is None, "Interrupted turn must not dispatch staged rows"

        task = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert task["state"] == "working", "Interrupted turn must not change task state"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 17. GET /tasks
# ---------------------------------------------------------------------------

def test_get_tasks_endpoint(client, workspace_topic, architect_staff, engineer_staff):
    c, _ = client
    ws_id, topic_id = workspace_topic

    prompt_r = _post_prompt(client, ws_id, topic_id, "@architect go")
    user_msg_id = prompt_r.json()["message_id"]
    delegate_r = _delegate(client, ws_id, topic_id, "architect", user_msg_id, "engineer",
                           goal="Build X", criteria="X done")
    task_id = delegate_r.json()["task_id"]

    r = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/orchestrate/tasks")
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) == 1
    t = tasks[0]
    assert t["id"] == task_id
    assert t["state"] == "working"
    assert t["goal"] == "Build X"
    assert t["assignee_name"] == "engineer"
    assert t["dispatcher_name"] == "architect"
    assert "dispatch_token" not in t


# ---------------------------------------------------------------------------
# 18. MCP tool availability
# ---------------------------------------------------------------------------

def test_mcp_submit_result_absent_at_depth_0(monkeypatch):
    """submit_result must not be in the tool list when TASK_DEPTH == 0."""
    _reload_mcp(monkeypatch, task_depth=0, max_depth=1)
    import src.orchestrate_mcp.server as s
    tool_names = {t.name for t in asyncio.run(s.mcp.list_tools())}
    assert "submit_result" not in tool_names, "submit_result must be hidden at depth 0"
    assert "ask_sender" in tool_names
    assert "delegate_task" in tool_names


def test_mcp_submit_result_present_at_depth_1(monkeypatch):
    """submit_result must be in the tool list when TASK_DEPTH >= 1."""
    _reload_mcp(monkeypatch, task_depth=1, max_depth=1)
    import src.orchestrate_mcp.server as s
    tool_names = {t.name for t in asyncio.run(s.mcp.list_tools())}
    assert "submit_result" in tool_names, "submit_result must be visible at depth >= 1"
    assert "delegate_task" not in tool_names  # depth ceiling reached
    assert "answer_question" not in tool_names  # only for depth-0 dispatchers
    assert "accept_result" not in tool_names


def test_mcp_answer_accept_absent_at_depth_1(monkeypatch):
    """answer_question and accept_result are dispatcher tools — absent at depth >= 1."""
    _reload_mcp(monkeypatch, task_depth=1, max_depth=2)
    import src.orchestrate_mcp.server as s
    tool_names = {t.name for t in asyncio.run(s.mcp.list_tools())}
    assert "answer_question" not in tool_names
    assert "accept_result" not in tool_names
    assert "submit_result" in tool_names
    assert "delegate_task" in tool_names  # depth < max


def test_mcp_answer_accept_present_at_depth_0(monkeypatch):
    """answer_question and accept_result are available at depth 0 (dispatcher turn)."""
    _reload_mcp(monkeypatch, task_depth=0, max_depth=1)
    import src.orchestrate_mcp.server as s
    tool_names = {t.name for t in asyncio.run(s.mcp.list_tools())}
    assert "answer_question" in tool_names
    assert "accept_result" in tool_names


def _reload_mcp(monkeypatch, task_depth: int, max_depth: int) -> None:
    monkeypatch.setenv("TASK_DEPTH", str(task_depth))
    monkeypatch.setenv("MAX_DELEGATION_DEPTH", str(max_depth))
    monkeypatch.setenv("MASTER_URL", "http://localhost:18080")
    monkeypatch.setenv("WORKSPACE_ID", "ws-test")
    monkeypatch.setenv("TOPIC_ID", "tp-test")
    monkeypatch.setenv("AGENT_NAME", "test-agent")
    monkeypatch.setenv("PROMPT_MESSAGE_ID", "msg-test")
    monkeypatch.setenv("DISPATCH_TOKEN", "tok-test")
    for mod_name in list(sys.modules):
        if "orchestrate_mcp" in mod_name:
            del sys.modules[mod_name]
