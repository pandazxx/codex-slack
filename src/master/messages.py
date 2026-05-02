from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .db import get_connection

router = APIRouter(
    prefix="/workspaces/{workspace_id}/topics/{topic_id}/messages",
    tags=["messages"],
)

_PROMPT_TOPIC = "codex-slack/workspace/{workspace_id}/topic/{topic_id}/prompt"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_or_create_session(conn, topic_id: str, agent_name: str, adapter: str) -> tuple[str, str | None]:
    row = conn.execute(
        "SELECT id, llm_session_id FROM sessions WHERE topic_id = ? AND agent_name = ?",
        (topic_id, agent_name),
    ).fetchone()
    if row:
        return row["id"], row["llm_session_id"]
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (id, topic_id, agent_name, adapter, llm_session_id, updated_at)"
        " VALUES (?, ?, ?, ?, NULL, ?)",
        (session_id, topic_id, agent_name, adapter, _now()),
    )
    conn.commit()
    return session_id, None


class MessageSend(BaseModel):
    text: str
    agent_name: str = "claude"


class MessageOut(BaseModel):
    id: str
    sender: str
    agent_name: str | None
    text: str
    created_at: str


@router.post("", status_code=202)
def send_message(workspace_id: str, topic_id: str, body: MessageSend, request: Request) -> dict:  # type: ignore[type-arg]
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        topic = conn.execute(
            "SELECT id, worktree_path FROM topics WHERE id = ? AND workspace_id = ?",
            (topic_id, workspace_id),
        ).fetchone()
        if topic is None:
            raise HTTPException(404, "topic not found")
        agent = conn.execute(
            "SELECT adapter, subagent FROM workspace_agents"
            " WHERE workspace_id = ? AND agent_name = ? AND active = 1",
            (workspace_id, body.agent_name),
        ).fetchone()
        if agent is None:
            raise HTTPException(404, f"agent '{body.agent_name}' not found in workspace")
        _session_id, llm_session_id = _get_or_create_session(
            conn, topic_id, body.agent_name, agent["adapter"]
        )
        message_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO messages"
            " (id, topic_id, sender, agent_name, text, transcript, attachments_json, created_at)"
            " VALUES (?, ?, 'user', NULL, ?, NULL, NULL, ?)",
            (message_id, topic_id, body.text.strip(), _now()),
        )
        conn.commit()
    finally:
        conn.close()

    payload = json.dumps({
        "message_id": message_id,
        "subagent": agent["subagent"],
        "worktree": topic["worktree_path"],
        "session_id": llm_session_id,
        "text": body.text.strip(),
        "attachments": [],
    })
    mqtt_topic = _PROMPT_TOPIC.format(workspace_id=workspace_id, topic_id=topic_id)
    request.app.state.mqtt.publish(mqtt_topic, payload, qos=1)

    return {"message_id": message_id, "status": "queued"}


@router.get("", response_model=list[MessageOut])
def list_messages(workspace_id: str, topic_id: str, request: Request) -> list[MessageOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        if conn.execute(
            "SELECT 1 FROM topics WHERE id = ? AND workspace_id = ?", (topic_id, workspace_id)
        ).fetchone() is None:
            raise HTTPException(404, "topic not found")
        rows = conn.execute(
            "SELECT id, sender, agent_name, text, created_at FROM messages"
            " WHERE topic_id = ? ORDER BY created_at",
            (topic_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        MessageOut(
            id=r["id"], sender=r["sender"], agent_name=r["agent_name"],
            text=r["text"], created_at=r["created_at"],
        )
        for r in rows
    ]
