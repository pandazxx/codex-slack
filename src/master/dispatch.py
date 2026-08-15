"""Core staff dispatch logic shared by the user-message path and the event worker.

Extracted from messages.py so that both direct user messages and event-triggered
dispatches travel an identical code path, giving event-triggered runs identical
session-sharing, MQTT contract, and agent-start behaviour.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from .agent_runner import (
    build_spawn_kwargs,
    container_name as _agent_container_name,
    ensure_agent_running,
)
from .db import get_connection

LOGGER = logging.getLogger(__name__)

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
    silent: bool = False,
    sender_kind: str | None = None,
    sender_name: str | None = None,
    receiver_kind: str | None = None,
    receiver_name: str | None = None,
    task_id: str | None = None,
    reply_to_message_id: str | None = None,
    dispatch_token: str | None = None,
) -> str:
    """Insert a message row, build the MQTT dispatch payload, broadcast on the hub, and publish.

    Returns the new message_id. Caller provides an already-resolved staff row.
    sender is 'user' for human-initiated messages or 'event' for event-triggered ones.
    raw_text is the text stored in messages.text; defaults to prompt_text when omitted.
    attachments is a list of attachment meta dicts for the payload; events pass None.

    sender_kind/sender_name/receiver_kind/receiver_name carry the explicit envelope
    introduced in ADR-0017. When omitted, defaults are derived from sender so that
    all existing callers continue to behave identically:
      sender='user'  → sender_kind='user'
      sender='event' → sender_kind='staff'
    receiver_kind defaults to 'staff' (the dispatched agent) when not provided.
    """
    if sender_kind is None:
        sender_kind = "user" if sender == "user" else "staff"
    if receiver_kind is None:
        receiver_kind = "staff"
    if receiver_name is None:
        receiver_name = staff["name"]
    if raw_text is None:
        raw_text = prompt_text
    if attachments is None:
        attachments = []
    if dispatch_token is None:
        dispatch_token = str(uuid.uuid4())

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

        # Ensure a sessions row exists for this topic+agent so the agent response
        # handler's UPDATE can store llm_session_id. INSERT OR IGNORE is a no-op on
        # subsequent turns when the row already exists.
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, topic_id, agent_name, adapter, llm_session_id, updated_at)"
            " VALUES (?, ?, ?, ?, NULL, ?)",
            (str(uuid.uuid4()), topic_id, staff["name"], staff["adapter"], _now()),
        )

        # For adapters like codex whose LLM session id differs from the internal
        # session_uuid, fetch the llm_session_id persisted by the agent response handler.
        # For workspace/global scope the session is shared across topics, so we search
        # the broadest matching set and take the most recently updated non-null id.
        llm_session_id: str | None = None
        if not is_new_session and session_uuid:
            scope = staff["session_scope"] or "topic"
            if scope == "workspace":
                row = conn.execute(
                    "SELECT s.llm_session_id FROM sessions s"
                    " JOIN topics t ON s.topic_id = t.id"
                    " WHERE t.workspace_id = ? AND s.agent_name = ?"
                    " AND s.llm_session_id IS NOT NULL"
                    " ORDER BY s.updated_at DESC LIMIT 1",
                    (workspace_id, staff["name"]),
                ).fetchone()
            elif scope == "global":
                row = conn.execute(
                    "SELECT llm_session_id FROM sessions"
                    " WHERE agent_name = ? AND llm_session_id IS NOT NULL"
                    " ORDER BY updated_at DESC LIMIT 1",
                    (staff["name"],),
                ).fetchone()
            else:
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
            " (id, topic_id, sender, agent_name, text, transcript, usage_json, attachments_json,"
            "  event_action_id, silent, created_at,"
            "  sender_kind, sender_name, receiver_kind, receiver_name, task_id, reply_to_message_id,"
            "  dispatch_token)"
            " VALUES (?, ?, ?, NULL, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id, topic_id, sender, raw_text, event_action_id, 1 if silent else 0, now,
                sender_kind, sender_name, receiver_kind, receiver_name, task_id, reply_to_message_id,
                dispatch_token,
            ),
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

    task_depth = 0
    if task_id:
        _task_conn = get_connection(app_state.db_path)
        try:
            _task_row = _task_conn.execute(
                "SELECT depth FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if _task_row is not None:
                task_depth = _task_row["depth"]
        finally:
            _task_conn.close()

    from .orchestration import get_effective_max_delegation_depth
    _cfg_conn = get_connection(app_state.db_path)
    try:
        max_delegation_depth = get_effective_max_delegation_depth(_cfg_conn, workspace_id)
    finally:
        _cfg_conn.close()

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
        "task_id": task_id,
        "task_depth": task_depth,
        "dispatch_token": dispatch_token,
        "max_delegation_depth": max_delegation_depth,
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
    # dispatch_token is excluded from WS frames and GET /messages — it is a
    # per-turn auth secret that must not leak to the frontend.
    await app_state.hub.broadcast("_global", {
        "type": "message",
        "topic_id": topic_id,
        "message_id": message_id,
        "sender": sender,
        "text": raw_text,
        "transcript": payload,
        "attachments": attachments,
        "silent": silent,
        "sender_kind": sender_kind,
        "sender_name": sender_name,
        "receiver_kind": receiver_kind,
        "receiver_name": receiver_name,
        "task_id": task_id,
        "reply_to_message_id": reply_to_message_id,
    })

    settings = app_state.settings
    ws_conn = get_connection(app_state.db_path)
    try:
        ws_row = ws_conn.execute(
            "SELECT container_name, repo_url FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        cname = (ws_row["container_name"] if ws_row else None) or _agent_container_name(workspace_id)
        repo_url = ws_row["repo_url"] if ws_row else ""
    finally:
        ws_conn.close()

    # Recover the agent before publishing: start it if merely stopped, or respawn it
    # if the container is gone (not_found) — otherwise the prompt is published to a
    # topic no one is subscribed to and silently lost (issue #251).
    try:
        spawn_kwargs = build_spawn_kwargs(
            settings=settings, db_path=app_state.db_path, workspace_id=workspace_id, repo_url=repo_url or "",
        )
        ensure_agent_running(name=cname, spawn_kwargs=spawn_kwargs, dry_run=settings.dry_run)
    except Exception:
        LOGGER.exception("dispatch.ensure_agent_failed container=%s", cname)

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
