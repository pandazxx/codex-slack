"""Workspace agent configuration management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .db import get_connection

router = APIRouter(
    prefix="/workspaces/{workspace_id}/agents",
    tags=["agents"],
)

_VALID_ADAPTERS = {"claude-code", "codex"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentCreate(BaseModel):
    agent_name: str
    adapter: str
    subagent: str | None = None


class AgentOut(BaseModel):
    id: str
    agent_name: str
    adapter: str
    subagent: str | None
    active: bool


@router.get("", response_model=list[AgentOut])
def list_agents(workspace_id: str, request: Request) -> list[AgentOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        rows = conn.execute(
            "SELECT id, agent_name, adapter, subagent, active FROM workspace_agents"
            " WHERE workspace_id = ? AND active = 1 ORDER BY created_at",
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        AgentOut(id=r["id"], agent_name=r["agent_name"], adapter=r["adapter"],
                 subagent=r["subagent"], active=bool(r["active"]))
        for r in rows
    ]


@router.post("", status_code=201, response_model=AgentOut)
def add_agent(workspace_id: str, body: AgentCreate, request: Request) -> AgentOut:
    name = body.agent_name.strip().lower()
    adapter = body.adapter.strip()
    if not name:
        raise HTTPException(422, "agent_name must not be empty")
    if adapter not in _VALID_ADAPTERS:
        raise HTTPException(422, f"adapter must be one of {sorted(_VALID_ADAPTERS)}")

    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone() is None:
            raise HTTPException(404, "workspace not found")

        existing = conn.execute(
            "SELECT id, active FROM workspace_agents WHERE workspace_id = ? AND agent_name = ?",
            (workspace_id, name),
        ).fetchone()

        now = _now()
        if existing:
            if existing["active"]:
                raise HTTPException(409, f"agent '{name}' already exists in this workspace")
            # Reactivate soft-deleted agent
            conn.execute(
                "UPDATE workspace_agents SET active = 1, adapter = ?, subagent = ?, deleted_at = NULL, created_at = ? WHERE id = ?",
                (adapter, body.subagent, now, existing["id"]),
            )
            conn.commit()
            agent_id = existing["id"]
        else:
            agent_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO workspace_agents (id, workspace_id, agent_name, adapter, subagent, active, created_at, deleted_at)"
                " VALUES (?, ?, ?, ?, ?, 1, ?, NULL)",
                (agent_id, workspace_id, name, adapter, body.subagent, now),
            )
            conn.commit()
    finally:
        conn.close()

    return AgentOut(id=agent_id, agent_name=name, adapter=adapter,
                    subagent=body.subagent, active=True)


@router.delete("/{agent_id}", status_code=204)
def remove_agent(workspace_id: str, agent_id: str, request: Request) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        row = conn.execute(
            "SELECT id, agent_name FROM workspace_agents WHERE id = ? AND workspace_id = ? AND active = 1",
            (agent_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "agent not found")
        conn.execute(
            "UPDATE workspace_agents SET active = 0, deleted_at = ? WHERE id = ?",
            (_now(), agent_id),
        )
        conn.commit()
    finally:
        conn.close()
