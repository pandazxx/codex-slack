from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .db import get_connection
from .staffs import resolve_staff, resolve_default_staff

_MENTION_RE = re.compile(r"^@(\S+)\s*(.*)", re.DOTALL)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/topics/{topic_id}/messages",
    tags=["messages"],
)

_PROMPT_TOPIC = "codex-slack/workspace/{workspace_id}/topic/{topic_id}/prompt"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_mention(text: str) -> tuple[str | None, str]:
    """Return (staff_name | None, cleaned_text). @mention prefix sets staff_name."""
    m = _MENTION_RE.match(text.strip())
    if m:
        return m.group(1).lower(), m.group(2).strip()
    return None, text.strip()


def _make_session_uuid(scope_type: str, scope_id: str, staff_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{scope_type}:{scope_id}:{staff_name}"))


def _get_staff_session(conn, scope_type: str, scope_id: str, staff_name: str) -> tuple[str, bool]:
    """Return (session_uuid, is_new_session). Creates staff_sessions row on first use."""
    row = conn.execute(
        "SELECT session_id FROM staff_sessions"
        " WHERE scope_type=? AND scope_id=? AND staff_name=?",
        (scope_type, scope_id, staff_name),
    ).fetchone()
    if row:
        return row["session_id"], False
    session_uuid = _make_session_uuid(scope_type, scope_id, staff_name)
    conn.execute(
        "INSERT INTO staff_sessions (scope_type, scope_id, staff_name, session_id, started_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (scope_type, scope_id, staff_name, session_uuid, _now()),
    )
    return session_uuid, True


def _staff_session_key(staff, workspace_id: str, topic_id: str) -> tuple[str, str]:
    """Map staff.session_scope to (scope_type, scope_id) for staff_sessions."""
    ss = (staff["session_scope"] or "topic")
    if ss == "workspace":
        return "workspace", workspace_id
    elif ss == "global":
        return "global", ""
    else:
        return "topic", topic_id


class AttachmentMeta(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int


class MessageOut(BaseModel):
    id: str
    sender: str
    agent_name: str | None
    text: str
    transcript: str | None
    created_at: str
    attachments: list[AttachmentMeta] = []


@router.post("", status_code=202)
async def send_message(
    workspace_id: str,
    topic_id: str,
    request: Request,
    text: str = Form(...),
    agent_name: str = Form(default="claude"),
    files: list[UploadFile] = File(default=[]),
) -> dict:  # type: ignore[type-arg]
    conn = get_connection(request.app.state.db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM workspaces WHERE id = ? AND archived_at IS NULL", (workspace_id,)
        ).fetchone() is None:
            raise HTTPException(404, "workspace not found")
        topic = conn.execute(
            "SELECT id, worktree_path, branch_name FROM topics"
            " WHERE id = ? AND workspace_id = ? AND archived_at IS NULL",
            (topic_id, workspace_id),
        ).fetchone()
        if topic is None:
            raise HTTPException(404, "topic not found")

        mention_name, prompt_text = _parse_mention(text)

        if mention_name:
            staff = resolve_staff(conn, mention_name, workspace_id, topic_id)
            if staff is None:
                raise HTTPException(404, f"staff '{mention_name}' not found")
        else:
            staff = resolve_default_staff(conn, workspace_id, topic_id)
            if staff is None:
                raise HTTPException(422, "no default staff configured for this workspace")

        ss_scope_type, ss_scope_id = _staff_session_key(staff, workspace_id, topic_id)
        session_uuid, is_new_session = _get_staff_session(conn, ss_scope_type, ss_scope_id, staff["name"])

        message_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO messages"
            " (id, topic_id, sender, agent_name, text, transcript, attachments_json, created_at)"
            " VALUES (?, ?, 'user', NULL, ?, NULL, NULL, ?)",
            (message_id, topic_id, text.strip(), _now()),
        )
        conn.commit()
    finally:
        conn.close()

    # Process file attachments
    attachment_metas: list[dict] = []  # type: ignore[type-arg]
    settings = request.app.state.settings
    store = request.app.state.attachment_store
    max_bytes: int = settings.attachment_max_size_bytes

    for f in files:
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=f"file '{f.filename}' exceeds size limit")
        attachment_id = str(uuid.uuid4())
        fname = f.filename or "upload"
        mime = f.content_type or "application/octet-stream"
        storage_uri = store.put(attachment_id, fname, data)

        att_conn = get_connection(request.app.state.db_path)
        try:
            att_conn.execute(
                "INSERT INTO attachments"
                " (id, message_id, topic_id, filename, mime_type, size_bytes, storage_uri, created_at, direction)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'inbound')",
                (attachment_id, message_id, topic_id, fname, mime, len(data), storage_uri, _now()),
            )
            att_conn.commit()
        finally:
            att_conn.close()

        attachment_metas.append({"id": attachment_id, "filename": fname, "mime_type": mime, "size_bytes": len(data)})

    payload = json.dumps({
        "message_id": message_id,
        "agent_name": staff["name"],
        "adapter": staff["adapter"],
        "subagent": staff["agent"],
        "worktree": topic["worktree_path"],
        "branch": topic["branch_name"],
        "session_id": session_uuid,
        "is_new_session": is_new_session,
        "session_scope": staff["session_scope"] or "topic",
        "model": staff["model"],
        "system_prompt": staff["system_prompt"],
        "text": prompt_text,
        "attachments": attachment_metas,
    })
    mqtt_topic = _PROMPT_TOPIC.format(workspace_id=workspace_id, topic_id=topic_id)
    request.app.state.mqtt.publish(mqtt_topic, payload, qos=1)

    return {"message_id": message_id, "status": "queued", "attachments": attachment_metas}


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
            "SELECT id, sender, agent_name, text, transcript, created_at FROM messages"
            " WHERE topic_id = ? ORDER BY created_at",
            (topic_id,),
        ).fetchall()
        result = []
        for r in rows:
            att_rows = conn.execute(
                "SELECT id, filename, mime_type, size_bytes FROM attachments WHERE message_id = ? ORDER BY created_at",
                (r["id"],),
            ).fetchall()
            attachments = [
                AttachmentMeta(
                    id=a["id"], filename=a["filename"],
                    mime_type=a["mime_type"], size_bytes=a["size_bytes"],
                )
                for a in att_rows
            ]
            result.append(
                MessageOut(
                    id=r["id"], sender=r["sender"], agent_name=r["agent_name"],
                    text=r["text"], transcript=r["transcript"], created_at=r["created_at"],
                    attachments=attachments,
                )
            )
    finally:
        conn.close()
    return result
