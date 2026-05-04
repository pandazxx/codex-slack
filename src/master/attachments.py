from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from .db import get_connection

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["attachments"])


class AttachmentOut(BaseModel):
    id: str
    message_id: str
    topic_id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: str
    direction: str


@router.get("/workspaces/{workspace_id}/topics/{topic_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(workspace_id: str, topic_id: str, request: Request) -> list[AttachmentOut]:
    conn = get_connection(request.app.state.db_path)
    try:
        rows = conn.execute(
            "SELECT id, message_id, topic_id, filename, mime_type, size_bytes, created_at, direction"
            " FROM attachments WHERE topic_id = ? ORDER BY created_at",
            (topic_id,),
        ).fetchall()
    finally:
        conn.close()
    return [AttachmentOut(**dict(r)) for r in rows]


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: str, request: Request) -> Response:
    conn = get_connection(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT filename, mime_type, storage_uri FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    store = request.app.state.attachment_store
    try:
        data = store.get(row["storage_uri"])
    except Exception:
        LOGGER.exception("attachment.read_failed id=%s", attachment_id)
        raise HTTPException(status_code=500, detail="could not read attachment")
    return Response(
        content=data,
        media_type=row["mime_type"],
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )
