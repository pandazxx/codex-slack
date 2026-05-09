from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .db import get_connection

MAX_EXPORT_BYTES = 16 * 1024 * 1024  # 16 MiB

router = APIRouter(tags=["topics"])

_SKIP_EVENT_TYPES = {"task_progress", "task_started", "retry_notice", "agent_result"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ThinkingBlock:
    text: str


@dataclass
class ToolUseBlock:
    tool_use_id: str
    name: str
    input: Any
    result: str | None = None


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
    date_str = date.today().strftime("%Y%m%d")
    return f"{ws_slug}-{topic_slug}-{date_str}.md"


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def _parse_transcript(
    transcript_json: str | None,
) -> tuple[list[ThinkingBlock], list[ToolUseBlock]]:
    if not transcript_json:
        return [], []
    try:
        events = json.loads(transcript_json)
    except (json.JSONDecodeError, TypeError):
        return [], []

    thinking_blocks: list[ThinkingBlock] = []
    tool_uses: dict[str, ToolUseBlock] = {}
    tool_use_order: list[str] = []

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type in _SKIP_EVENT_TYPES:
            continue

        if event_type == "assistant":
            content = event.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "thinking":
                    t = block.get("thinking") or block.get("text") or ""
                    if t:
                        thinking_blocks.append(ThinkingBlock(text=t))
                elif btype == "tool_use":
                    tuid = block.get("id") or block.get("tool_use_id", "")
                    name = block.get("name", "unknown")
                    inp = block.get("input")
                    tool_uses[tuid] = ToolUseBlock(tool_use_id=tuid, name=name, input=inp)
                    tool_use_order.append(tuid)

        elif event_type == "user":
            content = event.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tuid = block.get("tool_use_id", "")
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        parts = [
                            rc.get("text", "")
                            for rc in result_content
                            if isinstance(rc, dict) and rc.get("type") == "text"
                        ]
                        result_content = "\n".join(parts)
                    if tuid in tool_uses:
                        tool_uses[tuid].result = str(result_content)

    ordered_tools = [tool_uses[tid] for tid in tool_use_order if tid in tool_uses]
    return thinking_blocks, ordered_tools


# ---------------------------------------------------------------------------
# Fence helper
# ---------------------------------------------------------------------------

def _fenced(text: str, lang: str = "") -> str:
    max_run = 0
    current = 0
    for ch in text:
        if ch == "`":
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    fence = "`" * max(3, max_run + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


# ---------------------------------------------------------------------------
# Markdown renderer  (pure function — no DB access)
# ---------------------------------------------------------------------------

def render_markdown(
    ws_name: str,
    topic_subject: str,
    topic_id: str,
    messages: list[dict],  # type: ignore[type-arg]
) -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    subject = topic_subject or topic_id

    parts: list[str] = [
        f"# Topic: {subject}",
        f"Workspace: {ws_name}",
        f"Exported: {now_str}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        sender = msg.get("sender", "user")
        agent_name = msg.get("agent_name")
        text = msg.get("text") or ""
        created_at = msg.get("created_at", "")
        attachments = msg.get("attachments", [])

        if sender == "user":
            parts.append(f"## User — {created_at}")
            parts.append("")
            if text:
                parts.append(text)
        else:
            label = f"Agent ({agent_name})" if agent_name else "Agent"
            parts.append(f"## {label} — {created_at}")
            parts.append("")
            if text:
                parts.append(text)

            thinking_blocks, tool_blocks = _parse_transcript(msg.get("transcript"))

            if thinking_blocks:
                combined = "\n\n".join(b.text for b in thinking_blocks)
                parts += ["", "<details>", "<summary>Thinking</summary>", "", combined, "", "</details>"]

            for tb in tool_blocks:
                parts += ["", "<details>", f"<summary>Tool: {tb.name}</summary>", "", "**Input:**"]
                try:
                    parts.append(_fenced(json.dumps(tb.input, indent=2), "json"))
                except (TypeError, ValueError):
                    parts += ["<!-- raw input -->", _fenced(str(tb.input))]
                parts += ["", "**Output:**"]
                if tb.result is None:
                    parts.append("_(no result captured)_")
                else:
                    parts.append(_fenced(tb.result))
                parts += ["", "</details>"]

        if attachments:
            parts.append("")
            for att in attachments:
                fname = att.get("filename", "file")
                att_id = att.get("id", "")
                parts.append(f"- [{fname}](/attachments/{att_id}/download)")

        parts += ["", "---", ""]

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# API route
# ---------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/topics/{topic_id}/export")
def export_topic(
    workspace_id: str,
    topic_id: str,
    request: Request,
    format: str = "md",
) -> Response:
    if format != "md":
        raise HTTPException(status_code=422, detail="unsupported format")

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
            " WHERE topic_id = ? ORDER BY created_at, id",
            (topic_id,),
        ).fetchall()

        messages = []
        for r in msg_rows:
            att_rows = conn.execute(
                "SELECT id, filename FROM attachments WHERE message_id = ? ORDER BY created_at",
                (r["id"],),
            ).fetchall()
            messages.append({
                "id": r["id"],
                "sender": r["sender"],
                "agent_name": r["agent_name"],
                "text": r["text"],
                "transcript": r["transcript"],
                "created_at": r["created_at"],
                "attachments": [{"id": a["id"], "filename": a["filename"]} for a in att_rows],
            })
    finally:
        conn.close()

    body = render_markdown(
        ws_name=ws_row["name"],
        topic_subject=topic_row["subject"],
        topic_id=topic_id,
        messages=messages,
    )

    if len(body.encode("utf-8")) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="topic too large to export")

    fname = _slug_filename(ws_row["name"], topic_row["subject"], topic_id, workspace_id)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
