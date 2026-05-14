"""CRUD API for notes — freeform key/value pairs with tags at workspace or topic scope.

Workspace notes: /api/workspaces/{wid}/notes[/{key}]
Topic notes:     /api/workspaces/{wid}/topics/{tid}/notes[/{key}]

Note keys are immutable after creation; renames are delete-then-recreate.
Tags are stored as a JSON array and used to filter injection markers.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Pydantic models ───────────────────────────────────────────────────────────

class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value: str
    tags: list[str] = []


class NoteOut(BaseModel):
    key: str
    value: str
    tags: list[str]
    scope_type: str
    scope_id: str
    created_at: str
    updated_at: str


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None = None
    tags: list[str] | None = None


def _row_to_out(row) -> NoteOut:
    return NoteOut(
        key=row["key"],
        value=row["value"],
        tags=json.loads(row["tags"] or "[]"),
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── Workspace-scoped router ───────────────────────────────────────────────────

workspace_router = APIRouter(
    prefix="/workspaces/{workspace_id}/notes",
    tags=["notes"],
)


@workspace_router.get("", response_model=list[NoteOut])
async def list_workspace_notes(workspace_id: str, request: Request) -> list[NoteOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM notes WHERE scope_type='workspace' AND scope_id=? ORDER BY key",
            (workspace_id,),
        ).fetchall()
        return [_row_to_out(r) for r in rows]
    finally:
        conn.close()


@workspace_router.post("", response_model=NoteOut, status_code=201)
async def create_workspace_note(workspace_id: str, body: NoteIn, request: Request) -> NoteOut:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM notes WHERE scope_type='workspace' AND scope_id=? AND key=?",
            (workspace_id, body.key),
        ).fetchone():
            raise HTTPException(409, f"note {body.key!r} already exists in this workspace")
        now = _now()
        note_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO notes (id, scope_type, scope_id, key, value, tags, created_at, updated_at)"
            " VALUES (?, 'workspace', ?, ?, ?, ?, ?, ?)",
            (note_id, workspace_id, body.key, body.value, json.dumps(body.tags), now, now),
        )
        conn.commit()
        return _row_to_out(conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())
    finally:
        conn.close()


@workspace_router.get("/{key}", response_model=NoteOut)
async def get_workspace_note(workspace_id: str, key: str, request: Request) -> NoteOut:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT * FROM notes WHERE scope_type='workspace' AND scope_id=? AND key=?",
            (workspace_id, key),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"note {key!r} not found")
        return _row_to_out(row)
    finally:
        conn.close()


@workspace_router.patch("/{key}", response_model=NoteOut)
async def patch_workspace_note(
    workspace_id: str, key: str, body: NotePatch, request: Request
) -> NoteOut:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT * FROM notes WHERE scope_type='workspace' AND scope_id=? AND key=?",
            (workspace_id, key),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"note {key!r} not found")
        new_value = body.value if body.value is not None else row["value"]
        new_tags = json.dumps(body.tags) if body.tags is not None else row["tags"]
        now = _now()
        conn.execute(
            "UPDATE notes SET value=?, tags=?, updated_at=?"
            " WHERE scope_type='workspace' AND scope_id=? AND key=?",
            (new_value, new_tags, now, workspace_id, key),
        )
        conn.commit()
        return _row_to_out(
            conn.execute(
                "SELECT * FROM notes WHERE scope_type='workspace' AND scope_id=? AND key=?",
                (workspace_id, key),
            ).fetchone()
        )
    finally:
        conn.close()


@workspace_router.delete("/{key}", status_code=204)
async def delete_workspace_note(workspace_id: str, key: str, request: Request) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        if not conn.execute(
            "SELECT 1 FROM notes WHERE scope_type='workspace' AND scope_id=? AND key=?",
            (workspace_id, key),
        ).fetchone():
            raise HTTPException(404, f"note {key!r} not found")
        conn.execute(
            "DELETE FROM notes WHERE scope_type='workspace' AND scope_id=? AND key=?",
            (workspace_id, key),
        )
        conn.commit()
    finally:
        conn.close()


# ── Topic-scoped router ───────────────────────────────────────────────────────

topic_router = APIRouter(
    prefix="/workspaces/{workspace_id}/topics/{topic_id}/notes",
    tags=["notes"],
)


@topic_router.get("", response_model=list[NoteOut])
async def list_topic_notes(workspace_id: str, topic_id: str, request: Request) -> list[NoteOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM notes WHERE scope_type='topic' AND scope_id=? ORDER BY key",
            (topic_id,),
        ).fetchall()
        return [_row_to_out(r) for r in rows]
    finally:
        conn.close()


@topic_router.post("", response_model=NoteOut, status_code=201)
async def create_topic_note(
    workspace_id: str, topic_id: str, body: NoteIn, request: Request
) -> NoteOut:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM notes WHERE scope_type='topic' AND scope_id=? AND key=?",
            (topic_id, body.key),
        ).fetchone():
            raise HTTPException(409, f"note {body.key!r} already exists in this topic")
        now = _now()
        note_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO notes (id, scope_type, scope_id, key, value, tags, created_at, updated_at)"
            " VALUES (?, 'topic', ?, ?, ?, ?, ?, ?)",
            (note_id, topic_id, body.key, body.value, json.dumps(body.tags), now, now),
        )
        conn.commit()
        return _row_to_out(conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())
    finally:
        conn.close()


@topic_router.get("/{key}", response_model=NoteOut)
async def get_topic_note(
    workspace_id: str, topic_id: str, key: str, request: Request
) -> NoteOut:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT * FROM notes WHERE scope_type='topic' AND scope_id=? AND key=?",
            (topic_id, key),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"note {key!r} not found")
        return _row_to_out(row)
    finally:
        conn.close()


@topic_router.patch("/{key}", response_model=NoteOut)
async def patch_topic_note(
    workspace_id: str, topic_id: str, key: str, body: NotePatch, request: Request
) -> NoteOut:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT * FROM notes WHERE scope_type='topic' AND scope_id=? AND key=?",
            (topic_id, key),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"note {key!r} not found")
        new_value = body.value if body.value is not None else row["value"]
        new_tags = json.dumps(body.tags) if body.tags is not None else row["tags"]
        now = _now()
        conn.execute(
            "UPDATE notes SET value=?, tags=?, updated_at=?"
            " WHERE scope_type='topic' AND scope_id=? AND key=?",
            (new_value, new_tags, now, topic_id, key),
        )
        conn.commit()
        return _row_to_out(
            conn.execute(
                "SELECT * FROM notes WHERE scope_type='topic' AND scope_id=? AND key=?",
                (topic_id, key),
            ).fetchone()
        )
    finally:
        conn.close()


@topic_router.delete("/{key}", status_code=204)
async def delete_topic_note(
    workspace_id: str, topic_id: str, key: str, request: Request
) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        if not conn.execute(
            "SELECT 1 FROM notes WHERE scope_type='topic' AND scope_id=? AND key=?",
            (topic_id, key),
        ).fetchone():
            raise HTTPException(404, f"note {key!r} not found")
        conn.execute(
            "DELETE FROM notes WHERE scope_type='topic' AND scope_id=? AND key=?",
            (topic_id, key),
        )
        conn.commit()
    finally:
        conn.close()
