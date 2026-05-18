"""Core staff dispatch logic shared by the user-message path and the event worker.

Extracted from messages.py so that both direct user messages and event-triggered
dispatches travel an identical code path, giving event-triggered runs identical
session-sharing, MQTT contract, and agent-start behaviour.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .agent_runner import container_name as _agent_container_name, start_agent_if_stopped
from .db import get_connection

_PROMPT_TOPIC = "codex-slack/workspace/{workspace_id}/topic/{topic_id}/prompt"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_session_uuid(scope_type: str, scope_id: str, staff_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{scope_type}:{scope_id}:{staff_name}"))


def _get_staff_session(
    conn: sqlite3.Connection,
    scope_type: str,
    scope_id: str,
    staff_name: str,
) -> tuple[str, bool]:
    """Return (session_uuid, is_new_session). Creates staff_sessions row on first use."""
    row = conn.execute(
        "SELECT session_id FROM staff_sessions"
        " WHERE scope_type=? AND scope_id=? AND staff_name=?",
        (scope_type, scope_id, staff_name),
    ).fetchone()
    if row:
        return row["session_id"], False
    session_uuid = _make_session_uuid(scope_type, scope_id, staff_name)
    conn.execute(
        "INSERT INTO staff_sessions (scope_type, scope_id, staff_name, session_id, started_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (scope_type, scope_id, staff_name, session_uuid, _now()),
    )
    return session_uuid, True


def _staff_session_key(staff: sqlite3.Row, workspace_id: str, topic_id: str) -> tuple[str, str]:
    """Map staff.session_scope to (scope_type, scope_id) for staff_sessions."""
    ss = staff["session_scope"] or "topic"
    if ss == "workspace":
        return "workspace", workspace_id
    elif ss == "global":
        return "global", ""
    else:
        return "topic", topic_id


async def dispatch_to_staff(
    *,
    app_state,
    workspace_id: str,
    topic_id: str,
    staff: sqlite3.Row,
    prompt_text: str,
    sender: str,
    raw_text: str | None = None,
    attachments: list[dict] | None = None,
    event_action_id: str | None = None,
    response_mode: str | None = None,
) -> str:
    """Insert a message row, build the MQTT dispatch payload, broadcast on the hub, and publish.

    Returns the new message_id. Caller provides an already-resolved staff row.
    sender is 'user' for human-initiated messages or 'event' for event-triggered ones.
    raw_text is the text stored in messages.text; defaults to prompt_text when omitted.
    attachments is a list of attachment meta dicts for the payload; events pass None.
    """
    if raw_text is None:
        raw_text = prompt_text
    if attachments is None:
        attachments = []

    conn = get_connection(app_state.db_path)
    try:
        topic = conn.execute(
            "SELECT worktree_path, branch_name, repo_ref, base_sha FROM topics WHERE id = ?",
            (topic_id,),
        ).fetchone()
        if topic is None:
            raise ValueError(f"topic {topic_id!r} not found")

        if (staff["session_scope"] or "topic") == "none":
            session_uuid, is_new_session = None, True
        else:
            ss_scope_type, ss_scope_id = _staff_session_key(staff, workspace_id, topic_id)
            session_uuid, is_new_session = _get_staff_session(
                conn, ss_scope_type, ss_scope_id, staff["name"]
            )

        # For adapters like codex whose LLM session id differs from the internal
        # session_uuid, fetch the llm_session_id persisted by the agent response handler.
        llm_session_id: str | None = None
        if not is_new_session and session_uuid:
            row = conn.execute(
                "SELECT llm_session_id FROM sessions"
                " WHERE topic_id = ? AND agent_name = ?",
                (topic_id, staff["name"]),
            ).fetchone()
            if row:
                llm_session_id = row["llm_session_id"]

        message_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO messages"
            " (id, topic_id, sender, agent_name, text, transcript, usage_json, attachments_json, event_action_id, created_at)"
            " VALUES (?, ?, ?, NULL, ?, NULL, NULL, NULL, ?, ?)",
            (message_id, topic_id, sender, raw_text, event_action_id, now),
        )
        if sender == "user":
            conn.execute(
                "UPDATE workspaces SET last_message_at = ?, last_dispatched_at = ? WHERE id = ?",
                (now, now, workspace_id),
            )
        conn.commit()
    finally:
        conn.close()

    raw_system_prompt = staff["system_prompt"] or ""
    if raw_system_prompt:
        from .event_dispatcher import render_template  # local import — avoids circular dep
        raw_system_prompt = render_template(
            raw_system_prompt, {},
            db_path=app_state.db_path, workspace_id=workspace_id,
        )

    payload_dict: dict = {
        "message_id": message_id,
        "agent_name": staff["name"],
        "adapter": staff["adapter"],
        "subagent": staff["agent"],
        "worktree": topic["worktree_path"],
        "branch": topic["branch_name"],
        "repo_ref": topic["repo_ref"] or "",
        "base_sha": topic["base_sha"] or "",
        "session_id": llm_session_id or session_uuid,
        "is_new_session": is_new_session,
        "session_scope": staff["session_scope"] or "topic",
        "model": staff["model"],
        "system_prompt": raw_system_prompt or None,
        "text": prompt_text,
        "attachments": attachments,
    }
    if response_mode is not None:
        payload_dict["response_mode"] = response_mode
    payload = json.dumps(payload_dict)

    disp_conn = get_connection(app_state.db_path)
    try:
        disp_conn.execute(
            "UPDATE messages SET transcript = ? WHERE id = ?", (payload, message_id)
        )
        disp_conn.commit()
    finally:
        disp_conn.close()

    # Carries text/transcript/attachments so other tabs render the bubble immediately
    # without a refetch — see master's #155 fix for the user-message path.
    await app_state.hub.broadcast("_global", {
        "type": "message",
        "topic_id": topic_id,
        "message_id": message_id,
        "sender": sender,
        "text": raw_text,
        "transcript": payload,
        "attachments": attachments,
    })

    settings = app_state.settings
    ws_conn = get_connection(app_state.db_path)
    try:
        ws_row = ws_conn.execute(
            "SELECT container_name FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        cname = (ws_row["container_name"] if ws_row else None) or _agent_container_name(workspace_id)
    finally:
        ws_conn.close()

    try:
        start_agent_if_stopped(name=cname, dry_run=settings.dry_run)
    except Exception:
        pass

    mqtt_topic = _PROMPT_TOPIC.format(workspace_id=workspace_id, topic_id=topic_id)
    app_state.mqtt.publish(mqtt_topic, payload, qos=1)

    return message_id


async def post_message_direct(
    *,
    app_state,
    topic_id: str,
    text: str,
    sender: str = "agent",
    agent_name: str | None = None,
) -> str:
    """Insert a message row and broadcast it on the hub without MQTT dispatch.

    Used by the structured-output handler to post a staff-authored reply to the
    topic after parsing the JSON response, without triggering another LLM call.
    """
    conn = get_connection(app_state.db_path)
    try:
        message_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO messages"
            " (id, topic_id, sender, agent_name, text, transcript, usage_json, attachments_json, event_action_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)",
            (message_id, topic_id, sender, agent_name, text, now),
        )
        conn.commit()
    finally:
        conn.close()

    await app_state.hub.broadcast("_global", {
        "type": "message",
        "topic_id": topic_id,
        "message_id": message_id,
        "sender": sender,
        "text": text,
        "transcript": None,
        "attachments": [],
    })
    return message_id
