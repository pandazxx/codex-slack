"""CRUD API for event_actions — topic-scoped event trigger configurations.

Endpoints live under /api/workspaces/{wid}/topics/{tid}/event-actions following
the existing router pattern in staffs.py. Validation mirrors the DB CHECK
constraints with friendly HTTP errors; the CHECK constraints provide defence in
depth.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, model_validator

from .db import get_connection

router = APIRouter(
    prefix="/workspaces/{workspace_id}/topics/{topic_id}/event-actions",
    tags=["event-actions"],
)

_VALID_EVENT_TYPES = {
    "topic_message_sent",
    "topic_message_received",
    "topic_scheduler",
    "topic_archived",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Pydantic models ───────────────────────────────────────────────────────────

class EventActionIn(BaseModel):
    event_type: Literal[
        "topic_message_sent",
        "topic_message_received",
        "topic_scheduler",
        "topic_archived",
    ]
    staff_name: str
    prompt_template: str
    timing: Literal["before", "after"] | None = None
    cron_expr: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def validate_event_type_fields(self) -> "EventActionIn":
        et = self.event_type
        if et == "topic_scheduler":
            if not self.cron_expr:
                raise ValueError("cron_expr is required for topic_scheduler")
            if self.timing is not None:
                raise ValueError("timing must be null for topic_scheduler")
            _validate_cron(self.cron_expr)
        elif et == "topic_message_sent":
            if self.timing not in ("before", "after"):
                raise ValueError("timing must be 'before' or 'after' for topic_message_sent")
            if self.cron_expr is not None:
                raise ValueError("cron_expr must be null for topic_message_sent")
        elif et in ("topic_message_received", "topic_archived"):
            if self.timing not in (None, "after"):
                raise ValueError(f"timing must be null or 'after' for {et}")
            if self.cron_expr is not None:
                raise ValueError(f"cron_expr must be null for {et}")
        return self


class EventActionOut(BaseModel):
    id: str
    event_type: str
    scope_type: Literal["topic"]
    scope_id: str
    staff_name: str
    prompt_template: str
    timing: str | None
    cron_expr: str | None
    last_fired_at: str | None
    last_run_at: str | None
    last_run_status: str | None
    last_run_output: str | None
    enabled: bool
    created_at: str
    updated_at: str


class EventActionPatch(BaseModel):
    staff_name: str | None = None
    prompt_template: str | None = None
    timing: Literal["before", "after"] | None = None
    cron_expr: str | None = None
    enabled: bool | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_cron(cron_expr: str) -> None:
    from croniter import croniter
    if not croniter.is_valid(cron_expr):
        raise HTTPException(422, f"invalid cron expression: {cron_expr!r}")


def _require_topic(conn, workspace_id: str, topic_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM topics WHERE id = ? AND workspace_id = ?",
        (topic_id, workspace_id),
    ).fetchone() is None:
        raise HTTPException(404, "topic not found")


def _row_to_out(row) -> EventActionOut:
    return EventActionOut(
        id=row["id"],
        event_type=row["event_type"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        staff_name=row["staff_name"],
        prompt_template=row["prompt_template"],
        timing=row["timing"],
        cron_expr=row["cron_expr"],
        last_fired_at=row["last_fired_at"],
        last_run_at=row["last_run_at"],
        last_run_status=row["last_run_status"],
        last_run_output=row["last_run_output"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[EventActionOut])
def list_event_actions(workspace_id: str, topic_id: str, request: Request) -> list[EventActionOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        _require_topic(conn, workspace_id, topic_id)
        rows = conn.execute(
            "SELECT * FROM event_actions WHERE scope_type='topic' AND scope_id=? ORDER BY created_at",
            (topic_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_out(r) for r in rows]


@router.post("", status_code=201, response_model=EventActionOut)
def create_event_action(
    workspace_id: str,
    topic_id: str,
    body: EventActionIn,
    request: Request,
) -> EventActionOut:
    if body.cron_expr:
        _validate_cron(body.cron_expr)
    conn = get_connection(request.app.state.db_path)
    try:
        _require_topic(conn, workspace_id, topic_id)
        action_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO event_actions"
            " (id, event_type, scope_type, scope_id, staff_name, prompt_template,"
            "  timing, cron_expr, last_fired_at, last_run_at, last_run_status,"
            "  last_run_output, enabled, created_at, updated_at)"
            " VALUES (?, ?, 'topic', ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)",
            (
                action_id,
                body.event_type,
                topic_id,
                body.staff_name,
                body.prompt_template,
                body.timing,
                body.cron_expr,
                1 if body.enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM event_actions WHERE id = ?", (action_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_out(row)


@router.get("/{action_id}", response_model=EventActionOut)
def get_event_action(
    workspace_id: str,
    topic_id: str,
    action_id: str,
    request: Request,
) -> EventActionOut:
    conn = get_connection(request.app.state.db_path)
    try:
        _require_topic(conn, workspace_id, topic_id)
        row = conn.execute(
            "SELECT * FROM event_actions WHERE id = ? AND scope_id = ?",
            (action_id, topic_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "event action not found")
    return _row_to_out(row)


@router.patch("/{action_id}", response_model=EventActionOut)
def patch_event_action(
    workspace_id: str,
    topic_id: str,
    action_id: str,
    body: EventActionPatch,
    request: Request,
) -> EventActionOut:
    conn = get_connection(request.app.state.db_path)
    try:
        _require_topic(conn, workspace_id, topic_id)
        existing = conn.execute(
            "SELECT * FROM event_actions WHERE id = ? AND scope_id = ?",
            (action_id, topic_id),
        ).fetchone()
        if existing is None:
            raise HTTPException(404, "event action not found")

        # Merge patch fields onto the existing values.
        new_staff = body.staff_name if body.staff_name is not None else existing["staff_name"]
        new_template = body.prompt_template if body.prompt_template is not None else existing["prompt_template"]
        new_timing = body.timing if body.timing is not None else existing["timing"]
        new_cron = body.cron_expr if body.cron_expr is not None else existing["cron_expr"]
        new_enabled = (1 if body.enabled else 0) if body.enabled is not None else existing["enabled"]

        if new_cron:
            _validate_cron(new_cron)

        now = _now()
        conn.execute(
            "UPDATE event_actions"
            "   SET staff_name=?, prompt_template=?, timing=?, cron_expr=?, enabled=?, updated_at=?"
            " WHERE id=?",
            (new_staff, new_template, new_timing, new_cron, new_enabled, now, action_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM event_actions WHERE id = ?", (action_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_out(row)


@router.delete("/{action_id}", status_code=204)
def delete_event_action(
    workspace_id: str,
    topic_id: str,
    action_id: str,
    request: Request,
) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        _require_topic(conn, workspace_id, topic_id)
        row = conn.execute(
            "SELECT id FROM event_actions WHERE id = ? AND scope_id = ?",
            (action_id, topic_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "event action not found")
        conn.execute("DELETE FROM event_actions WHERE id = ?", (action_id,))
        conn.commit()
    finally:
        conn.close()
