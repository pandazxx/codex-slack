from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .db import get_connection

MAX_EXPORT_BYTES = 16 * 1024 * 1024  # 16 MiB

router = APIRouter(tags=["topics"])


# ---------------------------------------------------------------------------
# Slug / filename helpers
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 64) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len]


def _slug_filename(ws_name: str, topic_subject: str, topic_id: str, ws_id: str) -> str:
    ws_slug = _slugify(ws_name) or ws_id[:8]
    topic_slug = _slugify(topic_subject) or topic_id[:8]
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{ws_slug}-{topic_slug}-{date_str}.jsonl"


# ---------------------------------------------------------------------------
# JSONL renderer  (pure function — no DB access)
# ---------------------------------------------------------------------------

def render_jsonl(messages: list[dict]) -> str:  # type: ignore[type-arg]
    lines: list[str] = []
    for msg in messages:
        obj: dict = {  # type: ignore[type-arg]
            "type": "user" if msg.get("sender") == "user" else "agent",
            "timestamp": msg.get("created_at", ""),
        }
        if msg.get("agent_name"):
            obj["agent_name"] = msg["agent_name"]
        if msg.get("text"):
            obj["text"] = msg["text"]

        raw_transcript = msg.get("transcript")
        if raw_transcript:
            try:
                obj["transcript"] = json.loads(raw_transcript)
            except (json.JSONDecodeError, TypeError):
                obj["transcript"] = raw_transcript

        attachments = msg.get("attachments")
        if attachments:
            obj["attachments"] = attachments

        lines.append(json.dumps(obj, ensure_ascii=False))

    return "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# API route
# ---------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/topics/{topic_id}/export")
def export_topic(
    workspace_id: str,
    topic_id: str,
    request: Request,
) -> Response:
    conn = get_connection(request.app.state.db_path)
    try:
        ws_row = conn.execute(
            "SELECT id, name FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if ws_row is None:
            raise HTTPException(status_code=404, detail="workspace not found")

        topic_row = conn.execute(
            "SELECT id, subject FROM topics WHERE id = ? AND workspace_id = ?",
            (topic_id, workspace_id),
        ).fetchone()
        if topic_row is None:
            raise HTTPException(status_code=404, detail="topic not found")

        msg_rows = conn.execute(
            "SELECT id, sender, agent_name, text, transcript, created_at FROM messages"
            " WHERE topic_id = ? ORDER BY created_at, rowid",
            (topic_id,),
        ).fetchall()

        messages = []
        for r in msg_rows:
            att_rows = conn.execute(
                "SELECT id, filename, mime_type FROM attachments"
                " WHERE message_id = ? ORDER BY created_at",
                (r["id"],),
            ).fetchall()
            messages.append({
                "sender": r["sender"],
                "agent_name": r["agent_name"],
                "text": r["text"],
                "transcript": r["transcript"],
                "created_at": r["created_at"],
                "attachments": [
                    {"id": a["id"], "filename": a["filename"], "mime_type": a["mime_type"]}
                    for a in att_rows
                ] or None,
            })
    finally:
        conn.close()

    body = render_jsonl(messages)

    if len(body.encode("utf-8")) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="topic too large to export")

    fname = _slug_filename(ws_row["name"], topic_row["subject"], topic_id, workspace_id)
    return Response(
        content=body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
