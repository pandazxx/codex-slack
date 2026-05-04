"""Runtime key-value configuration — global and workspace scopes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .db import get_connection


def load_agent_env(db_path: str, workspace_id: str) -> dict[str, str]:
    """Return merged global+workspace config for use as agent container env vars."""
    conn = get_connection(db_path)
    try:
        return _merged_config(conn, workspace_id)
    finally:
        conn.close()


def load_global_env(db_path: str) -> dict[str, str]:
    """Return global config only (used when workspace_id is not yet known)."""
    conn = get_connection(db_path)
    try:
        return _get_global_config(conn)
    finally:
        conn.close()

global_router = APIRouter(prefix="/config", tags=["config"])
workspace_router = APIRouter(prefix="/workspaces/{workspace_id}/config", tags=["config"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConfigPatch(BaseModel):
    set: dict[str, str] = {}
    delete: list[str] = []


def _get_global_config(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM config WHERE scope_type='global' AND scope_id=''",
    ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def _get_workspace_config_raw(conn, workspace_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM config WHERE scope_type='workspace' AND scope_id=?",
        (workspace_id,),
    ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def _merged_config(conn, workspace_id: str) -> dict[str, str]:
    merged = _get_global_config(conn)
    merged.update(_get_workspace_config_raw(conn, workspace_id))
    return merged


def _valid_env_key(key: str) -> bool:
    return bool(key) and key.strip() == key and "=" not in key and "\x00" not in key


def _apply_patch(conn, scope_type: str, scope_id: str, patch: ConfigPatch) -> None:
    bad_set = [k for k in patch.set if not _valid_env_key(k)]
    bad_del = [k for k in patch.delete if not _valid_env_key(k)]
    if bad_set or bad_del:
        raise HTTPException(422, f"invalid env var key(s): {bad_set + bad_del}")
    now = _now()
    for key, value in patch.set.items():
        conn.execute(
            "INSERT INTO config (scope_type, scope_id, key, value, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(scope_type, scope_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (scope_type, scope_id, key, value, now),
        )
    for key in patch.delete:
        conn.execute(
            "DELETE FROM config WHERE scope_type=? AND scope_id=? AND key=?",
            (scope_type, scope_id, key),
        )
    conn.commit()


# ── Global config ─────────────────────────────────────────────────────────────

@global_router.get("", response_model=dict[str, str])
def get_global_config(request: Request) -> dict[str, str]:
    conn = get_connection(request.app.state.db_path)
    try:
        return _get_global_config(conn)
    finally:
        conn.close()


@global_router.patch("", response_model=dict[str, str])
def patch_global_config(body: ConfigPatch, request: Request) -> dict[str, str]:
    conn = get_connection(request.app.state.db_path)
    try:
        _apply_patch(conn, "global", "", body)
        return _get_global_config(conn)
    finally:
        conn.close()


# ── Workspace config ──────────────────────────────────────────────────────────

@workspace_router.get("", response_model=dict[str, str])
def get_workspace_config(workspace_id: str, request: Request) -> dict[str, str]:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        return _merged_config(conn, workspace_id)
    finally:
        conn.close()


@workspace_router.patch("", response_model=dict[str, str])
def patch_workspace_config(workspace_id: str, body: ConfigPatch, request: Request) -> dict[str, str]:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        _apply_patch(conn, "workspace", workspace_id, body)
        return _merged_config(conn, workspace_id)
    finally:
        conn.close()
