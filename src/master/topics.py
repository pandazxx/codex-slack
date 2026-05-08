from __future__ import annotations

import json
import logging
import re
import urllib.request
import uuid
from datetime import datetime, timezone

LOGGER = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .db import get_connection

router = APIRouter(prefix="/workspaces/{workspace_id}/topics", tags=["topics"])
recent_router = APIRouter(prefix="/topics", tags=["topics"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60] or "topic"


def _workspace_exists(conn, workspace_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM workspaces WHERE id = ? AND archived_at IS NULL", (workspace_id,)
    ).fetchone() is not None


def _workspace_exists_any(conn, workspace_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)
    ).fetchone() is not None


def _parse_github_repo(repo_url: str) -> tuple[str, str] | None:
    url = repo_url.strip().rstrip("/")
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    return None


def _fetch_ref_sha(repo_url: str, ref: str, token: str | None) -> str | None:
    parsed = _parse_github_repo(repo_url)
    if parsed is None:
        return None
    owner, repo = parsed
    api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
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
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return data.get("sha")
    except Exception:
        LOGGER.warning("topics.fetch_ref_sha_failed repo_url=%s ref=%s", repo_url, ref)
        return None


class TopicCreate(BaseModel):
    subject: str
    branch_name: str | None = None
    repo_ref: str


class TopicOut(BaseModel):
    id: str
    workspace_id: str
    subject: str
    branch_name: str
    repo_ref: str | None
    base_sha: str | None
    worktree_path: str
    created_at: str
    archived_at: str | None


def _row_to_topic(row) -> TopicOut:
    return TopicOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        subject=row["subject"],
        branch_name=row["branch_name"],
        repo_ref=row["repo_ref"],
        base_sha=row["base_sha"],
        worktree_path=row["worktree_path"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


@router.post("", status_code=201, response_model=TopicOut)
def create_topic(workspace_id: str, body: TopicCreate, request: Request) -> TopicOut:
    conn = get_connection(request.app.state.db_path)
    try:
        ws_row = conn.execute(
            "SELECT repo_url FROM workspaces WHERE id = ? AND archived_at IS NULL", (workspace_id,)
        ).fetchone()
        if ws_row is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        topic_id = str(uuid.uuid4())
        branch_name = (body.branch_name or f"topic/{_slugify(body.subject)}-{topic_id[:7]}").strip()
        repo_ref = body.repo_ref.strip()
        worktree_path = f"/workspace/worktrees/{topic_id}"
        now = _now()
        conn.execute(
            "INSERT INTO topics (id, workspace_id, subject, branch_name, repo_ref, worktree_path, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (topic_id, workspace_id, body.subject.strip(), branch_name, repo_ref, worktree_path, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()
    finally:
        conn.close()

    base_sha = _fetch_ref_sha(ws_row["repo_url"], repo_ref, request.app.state.settings.gh_token)
    if base_sha:
        conn2 = get_connection(request.app.state.db_path)
        try:
            conn2.execute("UPDATE topics SET base_sha = ? WHERE id = ?", (base_sha, topic_id))
            conn2.commit()
            row = conn2.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        finally:
            conn2.close()

    return _row_to_topic(row)


@router.get("", response_model=list[TopicOut])
def list_topics(workspace_id: str, request: Request, archived: bool = False) -> list[TopicOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        ws_check = _workspace_exists_any if archived else _workspace_exists
        if not ws_check(conn, workspace_id):
            raise HTTPException(status_code=404, detail="workspace not found")
        where = "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
        rows = conn.execute(
            f"SELECT * FROM topics WHERE workspace_id = ? AND {where} ORDER BY created_at",
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_topic(r) for r in rows]


@router.get("/{topic_id}", response_model=TopicOut)
def get_topic(workspace_id: str, topic_id: str, request: Request) -> TopicOut:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ? AND workspace_id = ?",
            (topic_id, workspace_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="topic not found")
    return _row_to_topic(row)


@router.delete("/{topic_id}", status_code=204)
def delete_topic(workspace_id: str, topic_id: str, request: Request) -> None:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT id, subject FROM topics WHERE id = ? AND workspace_id = ? AND archived_at IS NULL",
            (topic_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="topic not found")
        topic_name = row["subject"]
        conn.execute(
            "UPDATE topics SET archived_at = ? WHERE id = ?", (_now(), topic_id)
        )
        conn.commit()
    finally:
        conn.close()

    app_state = request.app.state
    if hasattr(app_state, "event_queue"):
        from .event_dispatcher import emit_event
        emit_event(
            app_state=app_state,
            event_type="topic_archived",
            topic_id=topic_id,
            workspace_id=workspace_id,
            timing="after",
            variables={"topic_name": topic_name},
        )


class RecentTopicOut(BaseModel):
    topic_id: str
    workspace_id: str
    workspace_name: str
    subject: str
    last_activity_at: str


@recent_router.get("/recent", response_model=list[RecentTopicOut])
def list_recent_topics(request: Request, limit: int = 20) -> list[RecentTopicOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        rows = conn.execute(
            """
            SELECT t.id AS topic_id, t.workspace_id, w.name AS workspace_name, t.subject,
                   COALESCE(MAX(m.created_at), t.created_at) AS last_activity_at
            FROM topics t
            JOIN workspaces w ON t.workspace_id = w.id
            LEFT JOIN messages m ON m.topic_id = t.id
            WHERE t.archived_at IS NULL AND w.archived_at IS NULL
            GROUP BY t.id
            ORDER BY last_activity_at DESC
            LIMIT ?
            """,
            (min(limit, 50),),
        ).fetchall()
    finally:
        conn.close()
    return [
        RecentTopicOut(
            topic_id=r["topic_id"],
            workspace_id=r["workspace_id"],
            workspace_name=r["workspace_name"],
            subject=r["subject"],
            last_activity_at=r["last_activity_at"],
        )
        for r in rows
    ]
