---
title: Native attachment management with pluggable storage
status: accepted
date: 2026-05-03
---

## Context

v2 handled file attachments via Slack/Discord APIs — inbound files were downloaded from the platform CDN, and large outbound responses were sent back as `.md` file attachments. This approach was entirely platform-dependent and is not available in v3.

Users need to upload documents for agent analysis (PDFs, images, code files, DOCX) and download agent-produced output files. These requirements exist independently of any chat platform.

Additionally, future deployment scenarios may require storage backends other than the local filesystem (e.g., S3-compatible object storage for multi-host or cloud deployments).

## Decision

Implement a native attachment system with the following properties:

1. **Inbound attachments** — users upload files via the web UI or REST API (`POST /api/attachments`). Files are stored by the master service and made available to agent prompts as file references or extracted text.

2. **Parsing and extraction** — text-extractable formats (PDF, DOCX, plain text, markdown, code) are parsed on upload and their content stored alongside the raw file for use in agent context.

3. **Persistence** — files are stored on a local filesystem path (`/opt/codex-slack/data/attachments/`) with metadata in the SQLite `attachments` table (id, filename, mime_type, size, storage_path, extracted_text, created_at).

4. **Outbound attachments** — agents can produce output files (e.g., generated code, reports). These are retrievable via `GET /api/attachments/{id}/download`.

5. **Pluggable storage** — the storage layer is abstracted behind an interface so the backend can be swapped from local filesystem to S3-compatible object storage without changing the API or DB schema. The storage path column stores a URI (`file://…` or `s3://…`) to make migration transparent.

6. **UI** — the chat view supports file upload (drag-and-drop or file picker), inline image preview, and download links for non-image attachments.

## Consequences

- New DB table `attachments` required; migration added to `init_db`.
- New API routes under `/api/attachments`.
- New storage abstraction module in `src/master/storage.py`.
- Parsing dependencies added to the master image (e.g., `pypdf`, `python-docx`).
- Frontend gains file upload widget and attachment display components.
- Local filesystem storage is the initial backend; S3 backend is a future addition.
