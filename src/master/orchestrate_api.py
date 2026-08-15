"""Internal REST endpoints for the orchestration MCP server.

Called by the in-container orchestrate MCP process over HTTP; not part of the
public API surface. Two endpoints per topic:

  POST /orchestrate/delegate  — create a task and dispatch the assignee's first prompt
  POST /orchestrate/ask       — post a clarifying question to the dispatcher or user
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .db import get_connection
from .orchestration import (
    MAX_DELEGATION_DEPTH,
    MAX_TASKS_PER_ROOT,
    detect_cycle,
    validate_envelope,
)
from .staffs import resolve_staff

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/topics/{topic_id}/orchestrate",
    tags=["orchestrate"],
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DelegateIn(BaseModel):
    caller_staff: str
    caller_message_id: str
    staff: str
    goal: str
    acceptance_criteria: str
    context: str | None = None


class DelegateOut(BaseModel):
    task_id: str
    state: str


class AskIn(BaseModel):
    caller_staff: str
    caller_message_id: str
    question: str


class AskOut(BaseModel):
    message_id: str
    task_state: str


def _build_prompt(goal: str, acceptance_criteria: str, context: str | None) -> str:
    parts = []
    if context:
        parts.append(f"**Context:**\n{context}")
    parts.append(f"**Goal:**\n{goal}")
    parts.append(f"**Acceptance criteria:**\n{acceptance_criteria}")
    return "\n\n".join(parts)


def _verify_caller_identity(
    conn,  # type: ignore[type-arg]
    caller_message_id: str,
    caller_staff: str,
    topic_id: str,
) -> None:
    """Bind the caller's claimed identity to a genuinely dispatched prompt row.

    Phase-a binding: the caller_message_id row must (a) exist, (b) belong to
    this topic, and (c) when the row carries identity fields (receiver_name or
    agent_name), at least one must match caller_staff.

    Rows without identity fields (legacy user messages, synthetic test rows
    without receiver_name/agent_name) bypass the name-match and are accepted;
    the residual gap for such rows is documented below.

    Residual gap: any network peer that knows a live message_id can forge the
    identity of its receiver.  The phase-(b) fix is a per-dispatch short-lived
    token sent inside the MQTT prompt payload and validated here — the message_id
    alone is then insufficient without the matching token.
    """
    row = conn.execute(
        "SELECT topic_id, receiver_name, agent_name FROM messages WHERE id = ?",
        (caller_message_id,),
    ).fetchone()
    if row is None:
        LOGGER.warning(
            "orchestration.guard_hit guard=caller_mismatch task_id=None topic_id=%s"
            " reason=unknown_message_id",
            topic_id,
        )
        raise HTTPException(422, "caller_mismatch")
    if row["topic_id"] != topic_id:
        LOGGER.warning(
            "orchestration.guard_hit guard=caller_mismatch task_id=None topic_id=%s"
            " reason=wrong_topic",
            topic_id,
        )
        raise HTTPException(422, "caller_mismatch")
    receiver_name = row["receiver_name"]
    agent_name = row["agent_name"]
    if (receiver_name is not None or agent_name is not None) and caller_staff not in (
        receiver_name,
        agent_name,
    ):
        LOGGER.warning(
            "orchestration.guard_hit guard=caller_mismatch task_id=None topic_id=%s"
            " reason=name_mismatch caller=%s",
            topic_id,
            caller_staff,
        )
        raise HTTPException(422, "caller_mismatch")


@router.post("/delegate", response_model=DelegateOut, status_code=200)
async def delegate_task(
    workspace_id: str,
    topic_id: str,
    body: DelegateIn,
    request: Request,
) -> DelegateOut:
    app_state = request.app.state
    db_path = app_state.db_path

    conn = get_connection(db_path)
    try:
        ws_row = conn.execute(
            "SELECT id FROM workspaces WHERE id = ? AND archived_at IS NULL",
            (workspace_id,),
        ).fetchone()
        if ws_row is None:
            raise HTTPException(404, "workspace not found")

        topic_row = conn.execute(
            "SELECT id FROM topics WHERE id = ? AND workspace_id = ? AND archived_at IS NULL",
            (topic_id, workspace_id),
        ).fetchone()
        if topic_row is None:
            raise HTTPException(404, "topic not found")

        _verify_caller_identity(conn, body.caller_message_id, body.caller_staff, topic_id)

        if body.caller_staff == body.staff:
            LOGGER.warning(
                "orchestration.guard_hit guard=self_delegation task_id=None topic_id=%s",
                topic_id,
            )
            raise HTTPException(422, "self_delegation")

        target_staff = resolve_staff(conn, body.staff, workspace_id, topic_id)
        if target_staff is None:
            LOGGER.warning(
                "orchestration.guard_hit guard=unknown_staff task_id=None topic_id=%s",
                topic_id,
            )
            raise HTTPException(404, "staff not found")

        # Determine caller depth from the prompt message row's task.
        caller_task_row = None
        caller_depth = 0
        prompt_row = conn.execute(
            "SELECT task_id FROM messages WHERE id = ?", (body.caller_message_id,)
        ).fetchone()
        if prompt_row and prompt_row["task_id"]:
            caller_task_row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (prompt_row["task_id"],)
            ).fetchone()
            if caller_task_row:
                caller_depth = caller_task_row["depth"]

        if caller_depth >= MAX_DELEGATION_DEPTH:
            LOGGER.warning(
                "orchestration.guard_hit guard=depth_exceeded task_id=%s topic_id=%s",
                caller_task_row["id"] if caller_task_row else None,
                topic_id,
            )
            raise HTTPException(422, "depth_exceeded")

        new_depth = caller_depth + 1

        # Fan-out fuse: count tasks under the same root.
        # When the caller has no task row (user-dispatched depth-0 turn), the
        # new depth-1 task is its own root and its dispatcher is the calling
        # staff.  A depth-0 'user' task row would only exist if created lazily,
        # which phase (a) never needs.
        root_task_id: str | None = None
        parent_task_id: str | None = None
        dispatcher_kind = "staff"
        dispatcher_name = body.caller_staff

        if caller_task_row:
            root_task_id = caller_task_row["root_task_id"]
            parent_task_id = caller_task_row["id"]
            count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE root_task_id = ?", (root_task_id,)
            ).fetchone()[0]
            if count >= MAX_TASKS_PER_ROOT:
                LOGGER.warning(
                    "orchestration.guard_hit guard=fan_out_exceeded task_id=%s topic_id=%s",
                    caller_task_row["id"],
                    topic_id,
                )
                raise HTTPException(422, "fan_out_exceeded")
        else:
            # Depth-0 caller has no parent task; root is set after task_id is minted below.
            root_task_id = None

        # Cycle detection: walk the parent_task_id chain from the caller's task
        # upward.  If the proposed assignee already appears as assignee_name or
        # dispatcher_name in any ancestor, reject with cycle_detected.  Unreachable
        # at MAX_DELEGATION_DEPTH=1 (depth-0 callers have no parent) but required
        # belt-and-braces for when the limit is raised in config.
        if caller_task_row:
            ancestors = []
            ancestor_id: str | None = caller_task_row["parent_task_id"]
            while ancestor_id:
                row = conn.execute(
                    "SELECT assignee_name, dispatcher_name, parent_task_id FROM tasks WHERE id = ?",
                    (ancestor_id,),
                ).fetchone()
                if row is None:
                    break
                ancestors.append(row)
                ancestor_id = row["parent_task_id"]
            if detect_cycle(ancestors, body.staff):
                LOGGER.warning(
                    "orchestration.guard_hit guard=cycle_detected task_id=%s topic_id=%s",
                    caller_task_row["id"],
                    topic_id,
                )
                raise HTTPException(422, "cycle_detected")

        try:
            validate_envelope(
                sender_kind="staff",
                sender_name=body.caller_staff,
                receiver_kind="staff",
                receiver_name=body.staff,
                # "pending" is a non-None placeholder so the cold_outreach check
                # passes; the real task_id has not been minted yet at this point.
                task_id="pending",
            )
        except ValueError as exc:
            LOGGER.warning(
                "orchestration.guard_hit guard=%s task_id=%s topic_id=%s",
                str(exc),
                caller_task_row["id"] if caller_task_row else None,
                topic_id,
            )
            raise HTTPException(422, str(exc)) from exc

        task_id = str(uuid.uuid4())
        if root_task_id is None:
            root_task_id = task_id
        now = _now()

        conn.execute(
            "INSERT INTO tasks"
            " (id, topic_id, root_task_id, parent_task_id, depth,"
            "  dispatcher_kind, dispatcher_name, assignee_name,"
            "  goal, acceptance_criteria, state, failure_score,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', 0.0, ?, ?)",
            (
                task_id, topic_id, root_task_id, parent_task_id, new_depth,
                dispatcher_kind, dispatcher_name, body.staff,
                body.goal, body.acceptance_criteria, now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    prompt_text = _build_prompt(body.goal, body.acceptance_criteria, body.context)

    from .dispatch import dispatch_to_staff
    await dispatch_to_staff(
        app_state=app_state,
        workspace_id=workspace_id,
        topic_id=topic_id,
        staff=target_staff,
        prompt_text=prompt_text,
        sender="event",
        sender_kind="staff",
        sender_name=body.caller_staff,
        receiver_kind="staff",
        receiver_name=body.staff,
        task_id=task_id,
        reply_to_message_id=body.caller_message_id,
    )

    state_conn = get_connection(db_path)
    try:
        state_conn.execute(
            "UPDATE tasks SET state = 'working', updated_at = ? WHERE id = ?",
            (_now(), task_id),
        )
        state_conn.commit()
    finally:
        state_conn.close()

    return DelegateOut(task_id=task_id, state="working")


@router.post("/ask", response_model=AskOut, status_code=200)
async def ask_sender(
    workspace_id: str,
    topic_id: str,
    body: AskIn,
    request: Request,
) -> AskOut:
    app_state = request.app.state
    db_path = app_state.db_path

    conn = get_connection(db_path)
    try:
        ws_row = conn.execute(
            "SELECT id FROM workspaces WHERE id = ? AND archived_at IS NULL",
            (workspace_id,),
        ).fetchone()
        if ws_row is None:
            raise HTTPException(404, "workspace not found")

        topic_row = conn.execute(
            "SELECT id FROM topics WHERE id = ? AND workspace_id = ? AND archived_at IS NULL",
            (topic_id, workspace_id),
        ).fetchone()
        if topic_row is None:
            raise HTTPException(404, "topic not found")

        _verify_caller_identity(conn, body.caller_message_id, body.caller_staff, topic_id)

        prompt_row = conn.execute(
            "SELECT task_id FROM messages WHERE id = ?", (body.caller_message_id,)
        ).fetchone()

        task_row = None
        if prompt_row and prompt_row["task_id"]:
            task_row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (prompt_row["task_id"],)
            ).fetchone()
    finally:
        conn.close()

    task_state = "n/a"
    receiver_kind = "user"
    receiver_name = None

    if task_row:
        receiver_kind = task_row["dispatcher_kind"]
        receiver_name = task_row["dispatcher_name"]
        task_state = "input-required"

    message_id = str(uuid.uuid4())

    ins_conn = get_connection(db_path)
    try:
        ins_conn.execute(
            "INSERT INTO messages"
            " (id, topic_id, sender, agent_name, text, transcript, usage_json, attachments_json,"
            "  silent, created_at,"
            "  sender_kind, sender_name, receiver_kind, receiver_name,"
            "  task_id, reply_to_message_id)"
            " VALUES (?, ?, 'agent', ?, ?, NULL, NULL, NULL, 0, ?,"
            "         'staff', ?, ?, ?, ?, ?)",
            (
                message_id, topic_id, body.caller_staff, body.question, _now(),
                body.caller_staff, receiver_kind, receiver_name,
                task_row["id"] if task_row else None,
                body.caller_message_id,
            ),
        )
        if task_row:
            ins_conn.execute(
                "UPDATE tasks SET state = 'input-required', updated_at = ? WHERE id = ?",
                (_now(), task_row["id"]),
            )
        ins_conn.commit()
    finally:
        ins_conn.close()

    await app_state.hub.broadcast("_global", {
        "type": "message",
        "topic_id": topic_id,
        "message_id": message_id,
        "sender": "agent",
        "agent_name": body.caller_staff,
        "text": body.question,
        "transcript": None,
        "attachments": [],
        "sender_kind": "staff",
        "sender_name": body.caller_staff,
        "receiver_kind": receiver_kind,
        "receiver_name": receiver_name,
        "task_id": task_row["id"] if task_row else None,
        "reply_to_message_id": body.caller_message_id,
    })

    # Phase (b) will dispatch the question to a staff dispatcher via dispatch_to_staff.
    # In phase (a) we only record + broadcast; re-entry is not yet wired.

    return AskOut(message_id=message_id, task_state=task_state)
