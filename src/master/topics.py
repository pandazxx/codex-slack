from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .db import get_connection

router = APIRouter(prefix="/workspaces/{workspace_id}/topics", tags=["topics"])


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
        "SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)
    ).fetchone() is not None


class TopicCreate(BaseModel):
    subject: str
    branch_name: str | None = None


class TopicOut(BaseModel):
    id: str
    workspace_id: str
    subject: str
    branch_name: str
    worktree_path: str
    created_at: str


def _row_to_topic(row) -> TopicOut:
    return TopicOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        subject=row["subject"],
        branch_name=row["branch_name"],
        worktree_path=row["worktree_path"],
        created_at=row["created_at"],
    )


@router.post("", status_code=201, response_model=TopicOut)
def create_topic(workspace_id: str, body: TopicCreate, request: Request) -> TopicOut:
    conn = get_connection(request.app.state.db_path)
    try:
        if not _workspace_exists(conn, workspace_id):
            raise HTTPException(status_code=404, detail="workspace not found")
        topic_id = str(uuid.uuid4())
        branch_name = (body.branch_name or _slugify(body.subject)).strip()
        worktree_path = f"/workspace/worktrees/{topic_id}"
        now = _now()
        conn.execute(
            "INSERT INTO topics (id, workspace_id, subject, branch_name, worktree_path, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (topic_id, workspace_id, body.subject.strip(), branch_name, worktree_path, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_topic(row)


@router.get("", response_model=list[TopicOut])
def list_topics(workspace_id: str, request: Request) -> list[TopicOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        if not _workspace_exists(conn, workspace_id):
            raise HTTPException(status_code=404, detail="workspace not found")
        rows = conn.execute(
            "SELECT * FROM topics WHERE workspace_id = ? ORDER BY created_at",
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
            "SELECT id FROM topics WHERE id = ? AND workspace_id = ?",
            (topic_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="topic not found")
        conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        conn.commit()
    finally:
        conn.close()
