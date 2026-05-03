---
title: Native attachment management with pluggable storage
status: accepted
date: 2026-05-03
---

## Context

v2 handled file attachments via Slack/Discord CDN. v3 needs a native system not tied to any platform. Users need to upload documents for agent analysis (PDFs, images, code files, DOCX) and agents need to be able to reference those files when generating responses.

Four design decisions were discussed and resolved before implementation began.

## Decisions

### 1. Attachment scope — message-scoped, upload-on-send

Attachments belong to a specific message, not to a topic or workspace. There is no reuse across messages. File and message text are sent together in a single `multipart/form-data` POST to the messages endpoint. Master stores the file and creates the message row in one atomic transaction. No pending/orphaned attachment state to manage.

### 2. Agent delivery — HTTP fetch + worktree placement

When master dispatches a prompt via MQTT, the payload includes attachment metadata (id, filename, mime_type) but no binary content. The agent fetches each attachment's raw file from master via `GET /api/attachments/{id}/download`, writes it into the worktree directory alongside the prompt, and tells claude the filename is present in the current directory. Claude reads and interprets the file using its own tools. No pre-extraction at any layer.

### 3. Text extraction — master does none

Master is a dumb file store. It receives the file, writes it to disk, records metadata, and returns. No parsing libraries (`pypdf`, `python-docx`, etc.) in the master image. All file interpretation happens inside the claude run, using claude's native Read tool and format understanding. This is more accurate than a library extraction and keeps master simple.

### 4. Outbound files — none in this slice

Agent output goes via git commits (code changes). No automatic mechanism to upload files claude writes during a run. A `.codex-output/` scanning convention is noted as a future addition when a concrete use case exists. Designing speculatively deferred.

## Schema

```sql
CREATE TABLE attachments (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id),
    topic_id    TEXT NOT NULL REFERENCES topics(id),
    filename    TEXT NOT NULL,
    mime_type   TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    storage_uri TEXT NOT NULL,   -- file:///… initially; s3://… future
    created_at  TEXT NOT NULL,
    direction   TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound'))
);
```

## Storage abstraction

Storage URI in the DB (`file://…` or `s3://…`) decouples the metadata from the backend. A thin `AttachmentStore` protocol in `src/master/storage.py` provides `put` / `get` / `delete`. `LocalAttachmentStore` is the initial implementation. S3 backend is a future addition requiring no schema or API changes.

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/workspaces/{id}/topics/{tid}/messages` | Extended to accept `multipart/form-data` with `text` field and optional `file` fields alongside existing JSON path |
| `GET` | `/api/attachments/{aid}/download` | Download raw file (used by agent and UI) |
| `GET` | `/api/workspaces/{id}/topics/{tid}/attachments` | List attachments for a topic (for UI display) |

## Consequences

- New `attachments` DB table; migration added to `init_db`.
- `POST /messages` accepts both `application/json` (no attachment) and `multipart/form-data` (with attachment).
- Agent MQTT loop updated to fetch and place attachment files before invoking claude.
- No new Python dependencies in master image.
- Local filesystem storage only for this slice; S3 migration path is schema-compatible.
- No outbound file mechanism this slice.
