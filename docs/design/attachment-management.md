# Attachment Management Design

## Context

v3 needs a native file attachment system not tied to any chat platform. This document covers the agreed design after a four-decision discussion session (2026-05-03). See ADR 0007 for the decision record.

## Goals

- Users upload files alongside a message (inbound) via UI or REST API
- Files are stored persistently and retrievable by ID
- Agent containers fetch the raw file and pass it to claude via worktree placement
- Storage backend is swappable (local filesystem → S3) without schema or API changes

## Non-goals (this slice)

- Text extraction or parsing in master
- Outbound agent-produced files (no `.codex-output/` scanning yet)
- File reuse across messages
- S3 storage backend (schema-compatible, deferred)
- Video or audio handling

## Decisions summary

| # | Decision |
|---|----------|
| Scope | Message-scoped, upload-on-send (multipart POST) |
| Agent delivery | Agent fetches via HTTP, places in worktree; claude reads natively |
| Extraction | Master stores only; no extraction anywhere |
| Outbound | None this slice; code output via git |

## DB Schema

```sql
CREATE TABLE IF NOT EXISTS attachments (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id),
    topic_id    TEXT NOT NULL REFERENCES topics(id),
    filename    TEXT NOT NULL,
    mime_type   TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    storage_uri TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    direction   TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound'))
);
```

`topic_id` is denormalized for efficient topic-level listing without joining through messages.

## Storage Abstraction

```python
# src/master/storage.py
from typing import Protocol

class AttachmentStore(Protocol):
    def put(self, attachment_id: str, filename: str, data: bytes) -> str:
        """Store data and return a storage_uri."""

    def get(self, storage_uri: str) -> bytes:
        """Retrieve raw bytes for the given URI."""

    def delete(self, storage_uri: str) -> None:
        """Remove the stored object."""


class LocalAttachmentStore:
    """Stores files under {base_dir}/{attachment_id}/{filename}.
    Returns URI: file://{base_dir}/{attachment_id}/{filename}
    """
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
```

The `storage_uri` stored in the DB is the only coupling between the metadata layer and the storage backend. Switching to S3 means deploying `S3AttachmentStore` and configuring it — no schema change, no API change.

## API

### POST /api/workspaces/{id}/topics/{tid}/messages

Extended to accept `multipart/form-data` in addition to the existing `application/json` body:

- `text` (string, required) — message text
- `file` (file, optional, repeatable) — one or more attachments

When files are present:
1. Master writes each file to storage, inserts into `attachments` with `message_id` set atomically
2. Returns the message object with an `attachments` array in the response

JSON-only callers (no files) are unaffected.

### GET /api/attachments/{aid}/download

Returns the raw file bytes with correct `Content-Type` and `Content-Disposition: attachment; filename=…` headers. Used by both the agent (HTTP fetch) and the browser (user download).

### GET /api/workspaces/{id}/topics/{tid}/attachments

Lists attachment metadata for a topic. Used by the UI to render the attachment list for the current topic.

## Agent Integration

MQTT prompt payload gains an `attachments` array:

```json
{
  "text": "Summarise the attached document",
  "attachments": [
    { "id": "abc123", "filename": "report.pdf", "mime_type": "application/pdf" }
  ]
}
```

Agent processing in `mqtt_loop.py`:

1. For each attachment in the payload, `GET /api/attachments/{id}/download` from master
2. Write the file to `{worktree}/{filename}`
3. Prepend a note to the prompt:

```
[Attached file: report.pdf — available in the current directory]

Summarise the attached document
```

4. Invoke claude as usual. Claude uses its Read tool (or vision for images) to access the file.
5. After the run, no output directory scanning (outbound deferred).

Master URL for agent HTTP calls is passed via environment variable `MASTER_URL` (e.g., `http://master:8080`).

## UI Changes

### TopicChat.vue

- Send form changes from plain `<textarea>` + button to a form that also accepts file input
- File picker or drag-and-drop zone below the textarea
- Selected files shown as chips with filename before sending
- On send: build `FormData`, append `text` and each `file`, POST as `multipart/form-data`
- Message bubbles for inbound messages show an attachment list below the text: image files previewed inline, others as a download link with filename and size

## Open Questions

- Should `direction = 'outbound'` rows ever be created in this slice? No — reserved for the future `.codex-output/` feature.
- Image vision: claude's Read tool handles text files. For images, claude's vision capability activates if the file is passed correctly. The worktree placement approach works as long as claude can see the file — worth verifying in the implementation spike.
- File size cap: recommend 20 MB server-side to keep the synchronous store fast. Configurable via `ATTACHMENT_MAX_SIZE_MB` env var.
