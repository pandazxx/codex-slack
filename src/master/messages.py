from __future__ import annotations

import json
import uuid
import re
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .db import get_connection
from .dispatch import dispatch_to_staff, _make_session_uuid  # re-exported for backwards compat
from .staffs import resolve_staff, resolve_default_staff

_MENTION_RE = re.compile(r"^@(\S+)\s*(.*)", re.DOTALL)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/topics/{topic_id}/messages",
    tags=["messages"],
)


def _parse_mention(text: str) -> tuple[str | None, str]:
    """Return (staff_name | None, cleaned_text). @mention prefix sets staff_name."""
    m = _MENTION_RE.match(text.strip())
    if m:
        return m.group(1).lower(), m.group(2).strip()
    return None, text.strip()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _topic_subject(db_path: str, topic_id: str) -> str:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT subject FROM topics WHERE id = ?", (topic_id,)).fetchone()
        return row["subject"] if row else ""
    finally:
        conn.close()


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
    usage_json: str | None = None
    interrupt_reason: str | None = None
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
        ws_row = conn.execute(
            "SELECT id, container_name FROM workspaces WHERE id = ? AND archived_at IS NULL",
            (workspace_id,),
        ).fetchone()
        if ws_row is None:
            raise HTTPException(404, "workspace not found")
        topic = conn.execute(
            "SELECT id, current_staff_name FROM topics"
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
        elif topic["current_staff_name"]:
            staff = resolve_staff(conn, topic["current_staff_name"], workspace_id, topic_id)
            if staff is None:
                raise HTTPException(404, f"current staff '{topic['current_staff_name']}' not found")
        else:
            staff = resolve_default_staff(conn, workspace_id, topic_id)
            if staff is None:
                raise HTTPException(422, "no default staff configured for this workspace")
    finally:
        conn.close()

    # Read file data into memory and build attachment metadata list.
    # Attachment DB rows are inserted after dispatch_to_staff creates the message row
    # so the FK message_id → messages.id is satisfied.
    settings = request.app.state.settings
    store = request.app.state.attachment_store
    max_bytes: int = settings.attachment_max_size_bytes

    attachment_uploads: list[tuple[str, str, str, int, str, bytes]] = []  # (id, fname, mime, size, storage_uri, data)
    attachment_metas: list[dict] = []  # type: ignore[type-arg]

    for f in files:
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=f"file '{f.filename}' exceeds size limit")
        attachment_id = str(uuid.uuid4())
        fname = f.filename or "upload"
        mime = f.content_type or "application/octet-stream"
        storage_uri = store.put(attachment_id, fname, data)
        attachment_uploads.append((attachment_id, fname, mime, len(data), storage_uri, data))
        attachment_metas.append({
            "id": attachment_id,
            "filename": fname,
            "mime_type": mime,
            "size_bytes": len(data),
        })

    # Emit topic_message_sent (before) — returns immediately, handled by the event worker.
    # Gate actions (before+structured_output=1) are excluded from the worker and run below.
    from .event_dispatcher import emit_event, run_gate_actions
    topic_name = _topic_subject(request.app.state.db_path, topic_id)
    _msg_json = json.dumps({"text": prompt_text, "sender": "user"})
    emit_event(
        app_state=request.app.state,
        event_type="topic_message_sent",
        topic_id=topic_id,
        workspace_id=workspace_id,
        timing="before",
        variables={"msgbody": prompt_text, "topic_name": topic_name, "message_json": _msg_json},
    )

    gate_passed = await run_gate_actions(
        app_state=request.app.state,
        topic_id=topic_id,
        workspace_id=workspace_id,
        variables={"msgbody": prompt_text, "topic_name": topic_name, "message_json": _msg_json},
    )
    if not gate_passed:
        return {"message_id": "", "status": "blocked", "attachments": [], "dispatch": None}

    message_id = await dispatch_to_staff(
        app_state=request.app.state,
        workspace_id=workspace_id,
        topic_id=topic_id,
        staff=staff,
        prompt_text=prompt_text,
        sender="user",
        raw_text=text.strip(),
        attachments=attachment_metas,
    )

    # Insert attachment rows now that the message row exists.
    if attachment_uploads:
        now = _now()
        att_conn = get_connection(request.app.state.db_path)
        try:
            for att_id, fname, mime, size, storage_uri, _ in attachment_uploads:
                att_conn.execute(
                    "INSERT INTO attachments"
                    " (id, message_id, topic_id, filename, mime_type, size_bytes, storage_uri, created_at, direction)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'inbound')",
                    (att_id, message_id, topic_id, fname, mime, size, storage_uri, now),
                )
            att_conn.commit()
        finally:
            att_conn.close()

    # Emit topic_message_sent (after).
    emit_event(
        app_state=request.app.state,
        event_type="topic_message_sent",
        topic_id=topic_id,
        workspace_id=workspace_id,
        timing="after",
        variables={"msgbody": prompt_text, "topic_name": topic_name, "message_json": _msg_json},
    )

    dispatch_payload = _read_transcript(request.app.state.db_path, message_id)

    return {
        "message_id": message_id,
        "status": "queued",
        "attachments": attachment_metas,
        "dispatch": dispatch_payload,
    }


def _read_transcript(db_path: str, message_id: str) -> str | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT transcript FROM messages WHERE id = ?", (message_id,)).fetchone()
        return row["transcript"] if row else None
    finally:
        conn.close()


@router.post("/{message_id}/cancel", status_code=202)
async def cancel_message(
    workspace_id: str,
    topic_id: str,
    message_id: str,
    request: Request,
) -> dict:  # type: ignore[type-arg]
    """Signal the agent to abort an in-progress response.

    Publishes a cancel MQTT message; the agent kills the running subprocess and
    publishes a normal (partial) response, which finalises the streaming bubble.
    """
    mqtt_cancel_topic = f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/cancel"
    request.app.state.mqtt.publish(
        mqtt_cancel_topic,
        json.dumps({"message_id": message_id}),
        qos=1,
    )
    return {"status": "cancel_requested", "message_id": message_id}


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
            "SELECT id, sender, agent_name, text, transcript, usage_json, interrupt_reason, created_at FROM messages"
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
                    text=r["text"], transcript=r["transcript"], usage_json=r["usage_json"],
                    interrupt_reason=r["interrupt_reason"],
                    created_at=r["created_at"], attachments=attachments,
                )
            )
    finally:
        conn.close()
    return result
