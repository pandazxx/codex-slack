"""Staff invocation profiles — runtime-configurable Claude/Codex invocation units."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from .db import get_connection
from .db._dialect import scope_id_match

_VALID_ADAPTERS = {"claude-code", "codex"}
_VALID_SESSION_SCOPES = {"topic", "workspace", "global", "none"}

_SELECT_COLS = (
    "id, scope_type, scope_id, name, adapter, model, system_prompt, agent,"
    " session_scope, is_default, extra_flags"
)

global_router = APIRouter(prefix="/staffs", tags=["staffs"])
workspace_router = APIRouter(prefix="/workspaces/{workspace_id}/staffs", tags=["staffs"])
topic_router = APIRouter(prefix="/workspaces/{workspace_id}/topics/{topic_id}/staffs", tags=["staffs"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StaffIn(BaseModel):
    name: str
    adapter: str = "claude-code"
    model: str | None = None
    system_prompt: str | None = None
    agent: str | None = None
    session_scope: str = "topic"
    is_default: bool = False
    extra_flags: str | None = None


class StaffOut(BaseModel):
    id: str
    scope_type: str
    scope_id: str | None
    name: str
    adapter: str
    model: str | None
    system_prompt: str | None
    agent: str | None
    session_scope: str
    is_default: bool
    extra_flags: str | None
    inherited_from: str | None = None


def _row_to_out(row: Any, inherited_from: str | None = None) -> StaffOut:
    return StaffOut(
        id=row["id"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        name=row["name"],
        adapter=row["adapter"],
        model=row["model"],
        system_prompt=row["system_prompt"],
        agent=row["agent"],
        session_scope=row["session_scope"],
        is_default=bool(row["is_default"]),
        extra_flags=row["extra_flags"],
        inherited_from=inherited_from,
    )


def _validate_staff_in(body: StaffIn) -> None:
    name = body.name.strip().lower()
    if not name:
        raise HTTPException(422, "name must not be empty")
    if body.adapter not in _VALID_ADAPTERS:
        raise HTTPException(422, f"adapter must be one of {sorted(_VALID_ADAPTERS)}")
    if body.session_scope not in _VALID_SESSION_SCOPES:
        raise HTTPException(422, f"session_scope must be one of {sorted(_VALID_SESSION_SCOPES)}")


def resolve_staff(
    conn: Any, name: str, workspace_id: str, topic_id: str | None
) -> Any | None:
    """Cascade resolution: topic → workspace → global. Returns first match."""
    if topic_id:
        row = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='topic' AND scope_id=:scope_id AND name=:name"
            ),
            {"scope_id": topic_id, "name": name},
        ).fetchone()
        if row:
            return row
    row = conn.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM staffs"
            " WHERE scope_type='workspace' AND scope_id=:scope_id AND name=:name"
        ),
        {"scope_id": workspace_id, "name": name},
    ).fetchone()
    if row:
        return row
    return conn.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM staffs"
            " WHERE scope_type='global' AND scope_id IS NULL AND name=:name"
        ),
        {"name": name},
    ).fetchone()


def resolve_default_staff(
    conn: Any, workspace_id: str, topic_id: str | None
) -> Any | None:
    """Cascade resolution for the default staff (is_default=TRUE)."""
    if topic_id:
        row = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='topic' AND scope_id=:scope_id AND is_default=TRUE"
            ),
            {"scope_id": topic_id},
        ).fetchone()
        if row:
            return row
    row = conn.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM staffs"
            " WHERE scope_type='workspace' AND scope_id=:scope_id AND is_default=TRUE"
        ),
        {"scope_id": workspace_id},
    ).fetchone()
    if row:
        return row
    return conn.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM staffs"
            " WHERE scope_type='global' AND scope_id IS NULL AND is_default=TRUE"
        ),
    ).fetchone()


def _insert_staff(
    conn: Any,
    body: StaffIn,
    scope_type: str,
    scope_id: str | None,
) -> StaffOut:
    name = body.name.strip().lower()
    now = _now()
    staff_id = str(uuid.uuid4())
    try:
        conn.execute(
            text(
                "INSERT INTO staffs"
                " (id, scope_type, scope_id, name, adapter, model, system_prompt, agent,"
                "  session_scope, is_default, extra_flags, created_at, updated_at)"
                " VALUES (:id, :scope_type, :scope_id, :name, :adapter, :model, :system_prompt, :agent,"
                "  :session_scope, :is_default, :extra_flags, :created_at, :updated_at)"
            ),
            {
                "id": staff_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "name": name,
                "adapter": body.adapter,
                "model": body.model,
                "system_prompt": body.system_prompt,
                "agent": body.agent,
                "session_scope": body.session_scope,
                "is_default": bool(body.is_default),
                "extra_flags": body.extra_flags,
                "created_at": now,
                "updated_at": now,
            },
        )
        conn.commit()
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, f"staff '{name}' already exists in this scope")
        raise
    row = conn.execute(
        text(f"SELECT {_SELECT_COLS} FROM staffs WHERE id=:id"), {"id": staff_id}
    ).fetchone()
    return _row_to_out(row)


def _update_staff(
    conn: Any,
    body: StaffIn,
    scope_type: str,
    scope_id: str | None,
    name: str,
) -> StaffOut:
    _scope_clause, _scope_params = scope_id_match(scope_id)
    row = conn.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM staffs"
            f" WHERE scope_type=:scope_type AND {_scope_clause} AND name=:name"
        ),
        {"scope_type": scope_type, "name": name, **_scope_params},
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"staff '{name}' not found")
    now = _now()
    conn.execute(
        text(
            "UPDATE staffs SET adapter=:adapter, model=:model, system_prompt=:system_prompt,"
            " agent=:agent, session_scope=:session_scope, is_default=:is_default,"
            " extra_flags=:extra_flags, updated_at=:updated_at WHERE id=:id"
        ),
        {
            "adapter": body.adapter, "model": body.model, "system_prompt": body.system_prompt,
            "agent": body.agent, "session_scope": body.session_scope,
            "is_default": bool(body.is_default),
            "extra_flags": body.extra_flags, "updated_at": now, "id": row["id"],
        },
    )
    conn.commit()
    updated = conn.execute(
        text(f"SELECT {_SELECT_COLS} FROM staffs WHERE id=:id"), {"id": row["id"]}
    ).fetchone()
    return _row_to_out(updated)


def _set_default_staff(
    conn: Any,
    scope_type: str,
    scope_id: str | None,
    name: str,
) -> StaffOut:
    _scope_clause, _scope_params = scope_id_match(scope_id)
    row = conn.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM staffs"
            f" WHERE scope_type=:scope_type AND {_scope_clause} AND name=:name"
        ),
        {"scope_type": scope_type, "name": name, **_scope_params},
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"staff '{name}' not found")
    now = _now()
    conn.execute(
        text(
            f"UPDATE staffs SET is_default=FALSE, updated_at=:updated_at"
            f" WHERE scope_type=:scope_type AND {_scope_clause}"
        ),
        {"updated_at": now, "scope_type": scope_type, **_scope_params},
    )
    conn.execute(
        text("UPDATE staffs SET is_default=TRUE, updated_at=:updated_at WHERE id=:id"),
        {"updated_at": now, "id": row["id"]},
    )
    conn.commit()
    updated = conn.execute(
        text(f"SELECT {_SELECT_COLS} FROM staffs WHERE id=:id"), {"id": row["id"]}
    ).fetchone()
    return _row_to_out(updated)


def _delete_staff(
    conn: Any,
    scope_type: str,
    scope_id: str | None,
    name: str,
) -> None:
    _scope_clause, _scope_params = scope_id_match(scope_id)
    row = conn.execute(
        text(
            "SELECT id FROM staffs"
            f" WHERE scope_type=:scope_type AND {_scope_clause} AND name=:name"
        ),
        {"scope_type": scope_type, "name": name, **_scope_params},
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"staff '{name}' not found")
    conn.execute(text("DELETE FROM staffs WHERE id=:id"), {"id": row["id"]})
    conn.commit()


# ── Global scope ──────────────────────────────────────────────────────────────

@global_router.get("", response_model=list[StaffOut])
def list_global_staffs(request: Request) -> list[StaffOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        rows = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='global' AND scope_id IS NULL ORDER BY name"
            ),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_out(r) for r in rows]


@global_router.post("", status_code=201, response_model=StaffOut)
def create_global_staff(body: StaffIn, request: Request) -> StaffOut:
    _validate_staff_in(body)
    conn = get_connection(request.app.state.db_path)
    try:
        return _insert_staff(conn, body, "global", None)
    finally:
        conn.close()


@global_router.put("/{name}", response_model=StaffOut)
def update_global_staff(name: str, body: StaffIn, request: Request) -> StaffOut:
    _validate_staff_in(body)
    conn = get_connection(request.app.state.db_path)
    try:
        return _update_staff(conn, body, "global", None, name)
    finally:
        conn.close()


@global_router.delete("/{name}", status_code=204)
def delete_global_staff(name: str, request: Request) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        _delete_staff(conn, "global", None, name)
    finally:
        conn.close()


@global_router.post("/{name}/set-default", response_model=StaffOut)
def set_default_global_staff(name: str, request: Request) -> StaffOut:
    conn = get_connection(request.app.state.db_path)
    try:
        return _set_default_staff(conn, "global", None, name)
    finally:
        conn.close()


# ── Workspace scope ───────────────────────────────────────────────────────────

@workspace_router.get("", response_model=list[StaffOut])
def list_workspace_staffs(workspace_id: str, request: Request) -> list[StaffOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM workspaces WHERE id=:id"), {"id": workspace_id}
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        ws_rows = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='workspace' AND scope_id=:scope_id ORDER BY name"
            ),
            {"scope_id": workspace_id},
        ).fetchall()
        ws_names = {r["name"] for r in ws_rows}
        global_rows = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='global' AND scope_id IS NULL ORDER BY name"
            ),
        ).fetchall()
    finally:
        conn.close()
    result = [_row_to_out(r) for r in ws_rows]
    result += [_row_to_out(r, inherited_from="global") for r in global_rows if r["name"] not in ws_names]
    return result


@workspace_router.post("", status_code=201, response_model=StaffOut)
def create_workspace_staff(workspace_id: str, body: StaffIn, request: Request) -> StaffOut:
    _validate_staff_in(body)
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM workspaces WHERE id=:id"), {"id": workspace_id}
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        return _insert_staff(conn, body, "workspace", workspace_id)
    finally:
        conn.close()


@workspace_router.put("/{name}", response_model=StaffOut)
def update_workspace_staff(workspace_id: str, name: str, body: StaffIn, request: Request) -> StaffOut:
    _validate_staff_in(body)
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM workspaces WHERE id=:id"), {"id": workspace_id}
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        return _update_staff(conn, body, "workspace", workspace_id, name)
    finally:
        conn.close()


@workspace_router.delete("/{name}", status_code=204)
def delete_workspace_staff(workspace_id: str, name: str, request: Request) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM workspaces WHERE id=:id"), {"id": workspace_id}
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        _delete_staff(conn, "workspace", workspace_id, name)
    finally:
        conn.close()


@workspace_router.post("/{name}/set-default", response_model=StaffOut)
def set_default_workspace_staff(workspace_id: str, name: str, request: Request) -> StaffOut:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM workspaces WHERE id=:id"), {"id": workspace_id}
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        return _set_default_staff(conn, "workspace", workspace_id, name)
    finally:
        conn.close()


# ── Topic scope ───────────────────────────────────────────────────────────────

@topic_router.get("", response_model=list[StaffOut])
def list_topic_staffs(workspace_id: str, topic_id: str, request: Request) -> list[StaffOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM topics WHERE id=:id AND workspace_id=:workspace_id"),
            {"id": topic_id, "workspace_id": workspace_id},
        ).fetchone() is None:
            raise HTTPException(404, "topic not found")
        topic_rows = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='topic' AND scope_id=:scope_id ORDER BY name"
            ),
            {"scope_id": topic_id},
        ).fetchall()
        topic_names = {r["name"] for r in topic_rows}
        ws_rows = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='workspace' AND scope_id=:scope_id ORDER BY name"
            ),
            {"scope_id": workspace_id},
        ).fetchall()
        ws_names = {r["name"] for r in ws_rows if r["name"] not in topic_names}
        global_rows = conn.execute(
            text(
                f"SELECT {_SELECT_COLS} FROM staffs"
                " WHERE scope_type='global' AND scope_id IS NULL ORDER BY name"
            ),
        ).fetchall()
    finally:
        conn.close()
    result = [_row_to_out(r) for r in topic_rows]
    result += [_row_to_out(r, inherited_from="workspace") for r in ws_rows if r["name"] not in topic_names]
    result += [
        _row_to_out(r, inherited_from="global")
        for r in global_rows
        if r["name"] not in topic_names and r["name"] not in ws_names
    ]
    return result


@topic_router.post("", status_code=201, response_model=StaffOut)
def create_topic_staff(workspace_id: str, topic_id: str, body: StaffIn, request: Request) -> StaffOut:
    _validate_staff_in(body)
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM topics WHERE id=:id AND workspace_id=:workspace_id"),
            {"id": topic_id, "workspace_id": workspace_id},
        ).fetchone() is None:
            raise HTTPException(404, "topic not found")
        return _insert_staff(conn, body, "topic", topic_id)
    finally:
        conn.close()


@topic_router.put("/{name}", response_model=StaffOut)
def update_topic_staff(workspace_id: str, topic_id: str, name: str, body: StaffIn, request: Request) -> StaffOut:
    _validate_staff_in(body)
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM topics WHERE id=:id AND workspace_id=:workspace_id"),
            {"id": topic_id, "workspace_id": workspace_id},
        ).fetchone() is None:
            raise HTTPException(404, "topic not found")
        return _update_staff(conn, body, "topic", topic_id, name)
    finally:
        conn.close()


@topic_router.delete("/{name}", status_code=204)
def delete_topic_staff(workspace_id: str, topic_id: str, name: str, request: Request) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            text("SELECT 1 FROM topics WHERE id=:id AND workspace_id=:workspace_id"),
            {"id": topic_id, "workspace_id": workspace_id},
        ).fetchone() is None:
            raise HTTPException(404, "topic not found")
        _delete_staff(conn, "topic", topic_id, name)
    finally:
        conn.close()
