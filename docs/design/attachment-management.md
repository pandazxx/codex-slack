# Attachment Management Design

## Context

v3 needs a native file attachment system that is not tied to any chat platform. Both inbound uploads (user → agent) and outbound downloads (agent → user) must be supported. Storage must be abstracted so the system can migrate from local filesystem to S3-compatible object storage without breaking the API or DB schema.

This design covers: API, DB schema, storage abstraction, text extraction, UI, and agent integration.

## Goals

- Users can upload files (PDF, DOCX, images, code, plain text) via UI or REST API
- Uploaded files are parsed/extracted for agent context where applicable
- Files are persistently stored and retrievable by ID
- Agent containers can reference uploaded files in prompts
- Agent-produced output files are downloadable from the UI
- Storage backend is swappable (local filesystem → S3) without schema or API changes

## Non-goals

- Real-time streaming of large files
- Video or audio processing
- Platform-specific file handling (Slack CDN, Discord CDN)

## Design

### DB Schema — `attachments` table

```sql
CREATE TABLE IF NOT EXISTS attachments (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    topic_id     TEXT REFERENCES topics(id),      -- NULL for workspace-scoped attachments
    message_id   TEXT REFERENCES messages(id),    -- NULL if not yet associated with a message
    filename     TEXT NOT NULL,
    mime_type    TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    storage_uri  TEXT NOT NULL,                   -- file:///… or s3://bucket/key
    extracted_text TEXT,                          -- NULL if not text-extractable
    created_at   TEXT NOT NULL,
    direction    TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound'))
);
```

### Storage Abstraction — `src/master/storage.py`

```python
class AttachmentStore(Protocol):
    def put(self, attachment_id: str, data: bytes, filename: str) -> str: ...  # returns storage_uri
    def get(self, storage_uri: str) -> bytes: ...
    def delete(self, storage_uri: str) -> None: ...

class LocalAttachmentStore:
    # Stores under /opt/codex-slack/data/attachments/{attachment_id}/{filename}
    # Returns file:///opt/codex-slack/data/attachments/{attachment_id}/{filename}
    ...

class S3AttachmentStore:
    # Future: stores in s3://{bucket}/attachments/{attachment_id}/{filename}
    ...
```

### Text Extraction

On upload, the master service attempts text extraction based on MIME type:

| MIME type | Extractor |
|-----------|-----------|
| `application/pdf` | `pypdf` |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `python-docx` |
| `text/*` | read directly |
| `image/*` | no extraction (pass raw to agent via vision if supported) |
| Other | store raw only |

Extracted text is stored in `attachments.extracted_text`. If extraction fails, the column is NULL and the raw file is still stored.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/workspaces/{id}/attachments` | Upload a file (multipart/form-data). Returns `AttachmentOut`. |
| `GET` | `/api/workspaces/{id}/attachments` | List attachments for a workspace. |
| `GET` | `/api/workspaces/{id}/attachments/{aid}` | Get attachment metadata. |
| `GET` | `/api/workspaces/{id}/attachments/{aid}/download` | Download raw file. |
| `DELETE` | `/api/workspaces/{id}/attachments/{aid}` | Delete attachment (soft or hard, TBD). |

Topic-scoped upload: include `topic_id` in the multipart form body.

### Agent Integration

When a message is sent to a topic that has attachments, the master service includes attachment references in the MQTT prompt payload:

```json
{
  "text": "Summarise the attached document",
  "attachments": [
    {
      "id": "abc123",
      "filename": "report.pdf",
      "mime_type": "application/pdf",
      "extracted_text": "…full extracted text…"
    }
  ]
}
```

The agent MQTT loop prepends extracted text to the prompt before passing to the LLM:

```
[Attachment: report.pdf]
…extracted text…

---
User: Summarise the attached document
```

For image attachments with no extracted text, the file path inside the agent container is passed (requires volume mount or file transfer — TBD in implementation slice).

### Outbound Attachments

Agents can produce output files by writing them to a designated output directory in the worktree. After the LLM run completes, the agent MQTT loop scans for new files in `{worktree}/.codex-output/`, uploads them via the master attachment API, and includes their IDs in the response payload. The UI then renders download links.

### UI Changes

- **Upload widget** in TopicChat: drag-and-drop zone or file picker button, shows filename and status while uploading
- **Attachment list** in TopicChat: displays uploaded files associated with the current topic
- **Inline image preview**: images displayed inline in the chat bubble
- **Download links**: non-image attachments shown as a download button with filename and size

## Alternatives Considered

- **Base64-encode files in MQTT payload** — rejected; MQTT has message size limits and base64 bloats payloads significantly.
- **Mount a shared volume between master and agent** — feasible for local deployment but breaks for multi-host; storage URI abstraction handles both.
- **Store files in SQLite BLOBs** — rejected; SQLite BLOBs degrade performance for large files and make S3 migration harder.

## Open Questions

- Image attachments: pass raw file to agent via vision API or just as a file path? Depends on whether the LLM adapter supports multimodal input.
- Outbound file detection: polling `{worktree}/.codex-output/` vs. agent explicitly publishing file paths in the response payload. Explicit publishing is cleaner.
- Soft-delete vs. hard-delete for attachments: align with workspace/topic soft-delete pattern (add `archived_at`)?
