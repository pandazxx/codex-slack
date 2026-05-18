from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import urllib.request
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .agent_runner import get_container_status, refresh_auth, spawn_agent, stop_agent, container_name
from .db import get_connection
from .runtime_config import load_agent_env
from .staffs import StaffOut, _SELECT_COLS as _STAFF_COLS, _row_to_out as _staff_row_to_out

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_DEFAULT_STAFFS = [
    # (name, adapter, is_default)
    ("claude", "claude-code", True),
    ("codex", "codex", False),
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WorkspaceCreate(BaseModel):
    name: str
    repo_url: str
    repo_ref: str = "master"


class WorkspaceOut(BaseModel):
    id: str
    name: str
    repo_url: str
    container_name: str | None
    created_at: str
    archived_at: str | None
    last_refreshed_at: str | None = None
    staffs: list[StaffOut]


def _fetch_workspace_any(conn, workspace_id: str) -> WorkspaceOut | None:
    row = conn.execute(
        "SELECT id, name, repo_url, container_name, created_at, archived_at, last_refreshed_at"
        " FROM workspaces WHERE id = ?",
        (workspace_id,),
    ).fetchone()
    if row is None:
        return None
    staff_rows = conn.execute(
        f"SELECT {_STAFF_COLS} FROM staffs"
        " WHERE scope_type='workspace' AND scope_id=? ORDER BY name",
        (workspace_id,),
    ).fetchall()
    return WorkspaceOut(
        id=row["id"], name=row["name"], repo_url=row["repo_url"],
        container_name=row["container_name"], created_at=row["created_at"],
        archived_at=row["archived_at"], last_refreshed_at=row["last_refreshed_at"],
        staffs=[_staff_row_to_out(s) for s in staff_rows],
    )


def _fetch_workspace(conn, workspace_id: str) -> WorkspaceOut | None:
    """Fetch an active (non-archived) workspace."""
    row = conn.execute(
        "SELECT id, name, repo_url, container_name, created_at, archived_at, last_refreshed_at"
        " FROM workspaces WHERE id = ? AND archived_at IS NULL",
        (workspace_id,),
    ).fetchone()
    if row is None:
        return None
    staff_rows = conn.execute(
        f"SELECT {_STAFF_COLS} FROM staffs"
        " WHERE scope_type='workspace' AND scope_id=? ORDER BY name",
        (workspace_id,),
    ).fetchall()
    return WorkspaceOut(
        id=row["id"], name=row["name"], repo_url=row["repo_url"],
        container_name=row["container_name"], created_at=row["created_at"],
        archived_at=row["archived_at"], last_refreshed_at=row["last_refreshed_at"],
        staffs=[_staff_row_to_out(s) for s in staff_rows],
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
        for name, adapter, is_default in _DEFAULT_STAFFS:
            conn.execute(
                "INSERT INTO staffs"
                " (id, scope_type, scope_id, name, adapter, model, system_prompt, agent,"
                "  session_scope, is_default, extra_flags, created_at, updated_at)"
                " VALUES (?, 'workspace', ?, ?, ?, NULL, NULL, NULL, 'topic', ?, NULL, ?, ?)",
                (str(uuid.uuid4()), workspace_id, name, adapter, 1 if is_default else 0, now, now),
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
            extra_env=load_agent_env(request.app.state.db_path, workspace_id),
            mem_limit=settings.agent_mem_limit or None,
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
def list_workspaces(request: Request, archived: bool = False) -> list[WorkspaceOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        where = "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
        rows = conn.execute(
            f"SELECT id FROM workspaces WHERE {where} ORDER BY created_at"
        ).fetchall()
        return [w for row in rows if (w := _fetch_workspace_any(conn, row["id"])) is not None]
    finally:
        conn.close()


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: str, request: Request) -> WorkspaceOut:
    conn = get_connection(request.app.state.db_path)
    try:
        result = _fetch_workspace_any(conn, workspace_id)
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
            "SELECT id, container_name FROM workspaces WHERE id = ? AND archived_at IS NULL",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        existing_container = row["container_name"] or container_name(workspace_id)
        now = _now()
        conn.execute(
            "UPDATE workspaces SET archived_at = ? WHERE id = ?", (now, workspace_id)
        )
        conn.execute(
            "UPDATE topics SET archived_at = ? WHERE workspace_id = ? AND archived_at IS NULL",
            (now, workspace_id),
        )
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


@router.post("/{workspace_id}/refresh-auth", status_code=200)
def refresh_workspace_auth(workspace_id: str, request: Request) -> dict:  # type: ignore[type-arg]
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT id, container_name FROM workspaces WHERE id = ? AND archived_at IS NULL",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        cname = row["container_name"] or container_name(workspace_id)
        now = _now()
        conn.execute(
            "UPDATE workspaces SET last_refreshed_at = ? WHERE id = ?", (now, workspace_id)
        )
        conn.commit()
    finally:
        conn.close()

    settings = request.app.state.settings
    db_cfg = load_agent_env(request.app.state.db_path, workspace_id)
    gh_token = settings.gh_token or db_cfg.get("GH_TOKEN")
    try:
        refresh_auth(name=cname, gh_token=gh_token, dry_run=settings.dry_run)
    except Exception:
        LOGGER.exception("workspace.refresh_auth_failed container=%s", cname)

    return {"refreshed_at": now}


@router.post("/{workspace_id}/restart-agent", status_code=200)
def restart_workspace_agent(workspace_id: str, request: Request) -> dict:  # type: ignore[type-arg]
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT id, repo_url, container_name FROM workspaces WHERE id = ? AND archived_at IS NULL",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        cname = row["container_name"] or container_name(workspace_id)
        repo_url = row["repo_url"]
    finally:
        conn.close()

    settings = request.app.state.settings
    try:
        stop_agent(runtime=settings.container_runtime, name=cname, dry_run=settings.dry_run)
    except Exception:
        LOGGER.exception("workspace.restart_stop_failed container=%s", cname)

    try:
        spawn_agent(
            runtime=settings.container_runtime,
            workspace_id=workspace_id,
            repo_url=repo_url,
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
            master_url=settings.master_url,
            extra_env=load_agent_env(request.app.state.db_path, workspace_id),
            mem_limit=settings.agent_mem_limit or None,
        )
    except Exception:
        LOGGER.exception("workspace.restart_spawn_failed workspace_id=%s", workspace_id)
        raise HTTPException(status_code=500, detail="failed to restart agent")

    LOGGER.info("workspace.restarted workspace_id=%s container=%s", workspace_id, cname)
    return {"restarted": True}


@router.get("/{workspace_id}/agent-status")
def get_agent_status(workspace_id: str, request: Request) -> dict:  # type: ignore[type-arg]
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT container_name, last_agent_state FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    name = row["container_name"] or container_name(workspace_id)
    settings = request.app.state.settings
    result = get_container_status(name=name, dry_run=settings.dry_run)
    if row["last_agent_state"] and result.get("status") == "running":
        result["status"] = row["last_agent_state"]
    return result


def _parse_github_repo(repo_url: str) -> tuple[str, str] | None:
    url = repo_url.strip().rstrip("/")
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    return None


def _fetch_github_branches(owner: str, repo: str, token: str | None) -> list[str]:
    branches: list[str] = []
    page = 1
    while page <= 5:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=100&page={page}"
        req = urllib.request.Request(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "codex-slack-master",
            },
        )
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data: list[dict] = json.loads(resp.read().decode())
        if not data:
            break
        branches.extend(b["name"] for b in data)
        if len(data) < 100:
            break
        page += 1
    return sorted(branches)


def _fetch_git_branches(repo_url: str, token: str | None) -> list[str]:
    url = repo_url.strip()
    if token:
        m = re.match(r"(https?://)github\.com/(.+)", url)
        if m:
            url = f"{m.group(1)}x-access-token:{token}@github.com/{m.group(2)}"
    result = subprocess.run(
        ["git", "ls-remote", "--heads", url],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-remote failed: {result.stderr.strip()}")
    branches = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            branches.append(parts[1][len("refs/heads/"):])
    return sorted(branches)


@router.get("/{workspace_id}/branches", response_model=list[str])
def list_workspace_branches(workspace_id: str, request: Request) -> list[str]:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT repo_url FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    repo_url: str = row["repo_url"]
    settings = request.app.state.settings
    # Honor the UI-configured token (stored in runtime_config) when the
    # process env doesn't carry GH_TOKEN. Matches refresh-auth's resolution.
    db_cfg = load_agent_env(request.app.state.db_path, workspace_id)
    gh_token = settings.gh_token or db_cfg.get("GH_TOKEN")
    parsed = _parse_github_repo(repo_url)
    if parsed is not None:
        owner, repo = parsed
        try:
            return _fetch_github_branches(owner, repo, gh_token)
        except Exception:
            LOGGER.warning(
                "workspaces.github_branches_failed owner=%s repo=%s, falling back to git ls-remote",
                owner,
                repo,
            )
    try:
        return _fetch_git_branches(repo_url, gh_token)
    except Exception:
        LOGGER.exception("workspaces.git_branches_failed repo_url=%s", repo_url)
        raise HTTPException(status_code=502, detail="failed to fetch branches")
