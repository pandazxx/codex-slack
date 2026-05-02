from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .agent_runner import spawn_agent, stop_agent, container_name
from .db import get_connection

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_DEFAULT_AGENTS = [
    ("claude", "claude-code", None),
    ("codex", "codex", None),
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WorkspaceCreate(BaseModel):
    name: str
    repo_url: str
    repo_ref: str = "master"


class WorkspaceAgentOut(BaseModel):
    id: str
    agent_name: str
    adapter: str
    subagent: str | None
    active: bool


class WorkspaceOut(BaseModel):
    id: str
    name: str
    repo_url: str
    container_name: str | None
    created_at: str
    agents: list[WorkspaceAgentOut]


def _fetch_workspace(conn, workspace_id: str) -> WorkspaceOut | None:
    row = conn.execute(
        "SELECT id, name, repo_url, container_name, created_at FROM workspaces WHERE id = ?",
        (workspace_id,),
    ).fetchone()
    if row is None:
        return None
    agents = conn.execute(
        "SELECT id, agent_name, adapter, subagent, active FROM workspace_agents"
        " WHERE workspace_id = ? AND active = 1",
        (workspace_id,),
    ).fetchall()
    return WorkspaceOut(
        id=row["id"],
        name=row["name"],
        repo_url=row["repo_url"],
        container_name=row["container_name"],
        created_at=row["created_at"],
        agents=[
            WorkspaceAgentOut(
                id=a["id"],
                agent_name=a["agent_name"],
                adapter=a["adapter"],
                subagent=a["subagent"],
                active=bool(a["active"]),
            )
            for a in agents
        ],
    )


@router.post("", status_code=201, response_model=WorkspaceOut)
def create_workspace(body: WorkspaceCreate, request: Request) -> WorkspaceOut:
    workspace_id = str(uuid.uuid4())
    now = _now()
    conn = get_connection(request.app.state.db_path)
    try:
        try:
            conn.execute(
                "INSERT INTO workspaces (id, name, repo_url, container_name, created_at)"
                " VALUES (?, ?, ?, NULL, ?)",
                (workspace_id, body.name.strip(), body.repo_url.strip(), now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="workspace name already exists")
        for agent_name, adapter, subagent in _DEFAULT_AGENTS:
            conn.execute(
                "INSERT INTO workspace_agents"
                " (id, workspace_id, agent_name, adapter, subagent, active, created_at, deleted_at)"
                " VALUES (?, ?, ?, ?, ?, 1, ?, NULL)",
                (str(uuid.uuid4()), workspace_id, agent_name, adapter, subagent, now),
            )
        conn.commit()
        result = _fetch_workspace(conn, workspace_id)
    finally:
        conn.close()
    assert result is not None

    settings = request.app.state.settings
    try:
        name = spawn_agent(
            runtime=settings.container_runtime,
            workspace_id=workspace_id,
            repo_url=body.repo_url.strip(),
            repo_ref=body.repo_ref.strip(),
            image=settings.agent_base_image,
            mqtt_host=settings.mqtt_host,
            mqtt_port=settings.mqtt_port,
            network=settings.agent_network,
            claude_code_oauth_token=settings.claude_code_oauth_token,
            anthropic_api_key=settings.anthropic_api_key,
            openai_api_key=settings.openai_api_key,
            gh_token=settings.gh_token,
            ssh_auth_sock_path=settings.agent_ssh_auth_sock_path,
            ssh_known_hosts_path=settings.agent_ssh_known_hosts_path,
            dry_run=settings.dry_run,
        )
        conn2 = get_connection(request.app.state.db_path)
        try:
            conn2.execute(
                "UPDATE workspaces SET container_name = ? WHERE id = ?",
                (name, workspace_id),
            )
            conn2.commit()
            result = result.model_copy(update={"container_name": name})
        finally:
            conn2.close()
    except Exception:
        LOGGER.exception("workspace.agent_spawn_failed workspace_id=%s", workspace_id)

    return result


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(request: Request) -> list[WorkspaceOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM workspaces ORDER BY created_at"
        ).fetchall()
        return [w for row in rows if (w := _fetch_workspace(conn, row["id"])) is not None]
    finally:
        conn.close()


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: str, request: Request) -> WorkspaceOut:
    conn = get_connection(request.app.state.db_path)
    try:
        result = _fetch_workspace(conn, workspace_id)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return result


@router.delete("/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: str, request: Request) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT id, container_name FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        existing_container = row["container_name"] or container_name(workspace_id)
        topic_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM topics WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()]
        for tid in topic_ids:
            conn.execute("DELETE FROM messages WHERE topic_id = ?", (tid,))
            conn.execute("DELETE FROM sessions WHERE topic_id = ?", (tid,))
        conn.execute("DELETE FROM topics WHERE workspace_id = ?", (workspace_id,))
        conn.execute("DELETE FROM workspace_agents WHERE workspace_id = ?", (workspace_id,))
        conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        conn.commit()
    finally:
        conn.close()

    settings = request.app.state.settings
    try:
        stop_agent(
            runtime=settings.container_runtime,
            name=existing_container,
            dry_run=settings.dry_run,
        )
    except Exception:
        LOGGER.exception("workspace.agent_stop_failed container=%s", existing_container)
