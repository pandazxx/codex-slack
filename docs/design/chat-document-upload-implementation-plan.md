# Chat Document Upload Implementation Design

**Status:** draft  
**Author:** Codex architect  
**Date:** 2026-03-28  
**Related ADRs:** `docs/decisions/0001-stage-uploaded-documents-and-convert-in-agent.md`

## Goal

Implement support for `docx` and `pdf` uploads in Slack and Discord so that:

- master stages uploaded documents into request-scoped storage outside the repo
- agent discovers staged attachments through `AGENT_REQUEST_MANIFEST`
- agent converts documents with `Mammoth` and `PyMuPDF4LLM`
- agent reads and edits the derived Markdown artifact
- when modifications are requested, the agent can commit the derived artifact and return a GitHub URL

## Current Integration Points

The current code already provides the right hooks:

- [src/master/slack_app.py](/workspace/repo/src/master/slack_app.py)
  Handles Slack events and currently extracts image attachments.
- [src/master/discord_app.py](/workspace/repo/src/master/discord_app.py)
  Handles Discord messages and currently reads text attachments inline plus image extraction.
- [src/master/router.py](/workspace/repo/src/master/router.py)
  Owns prompt dispatch and already stages Slack private images into the container.
- [src/agent/worker.py](/workspace/repo/src/agent/worker.py)
  Owns agent startup stages and can prepare request storage roots and runtime prerequisites.

## High-Level Design

### Master responsibilities

- detect supported document attachments from Slack and Discord
- manage request-scoped storage through a mounted request area attached to the agent container
- download and stage them into request-scoped storage
- write a request manifest JSON file
- inject `AGENT_REQUEST_MANIFEST` into the dispatch environment
- clean up the request directory after the reply is complete
- keep the routed prompt close to the user’s original text

### Agent responsibilities

- read `AGENT_REQUEST_MANIFEST`
- ingest each staged `docx` / `pdf`
- convert to Markdown and extracted assets
- work from derived artifacts
- commit derived artifacts if the task requires durable output

## Request Storage Layout

Request-specific storage lives outside the repo, under `/workspace/message/`, and is exposed to the agent through a master-managed mount created when the agent container starts.

Recommended layout:

```text
/workspace/message/
  <request-id>/
    manifest.json
    source/
      file-001.docx
      file-002.pdf
    derived/
      <attachment-id>/
        document.md
        manifest.json
        assets/
          image-001.png
```

### Why this layout

- transient request inputs stay outside the Git worktree
- multiple attachments can share one request directory
- derived artifacts are easy to inspect and clean up
- the agent can still read the paths directly
- the mount lifetime is per agent, while the request directory lifetime is per message

## Request ID Scheme

Use a deterministic request id per routed message.

### Slack

Suggested format:

```text
slack-<channel-id>-<thread-ts-or-event-ts>-<event-ts>
```

### Discord

Suggested format:

```text
discord-<channel-id>-<message-id>
```

### Requirements

- filesystem-safe
- unique per routed request
- usable in logs and debug output

## Request Manifest Schema

Master writes one manifest JSON file per routed request.

Suggested schema:

```json
{
  "request_id": "slack-C123-171234-171235",
  "platform": "slack",
  "channel_id": "C123",
  "thread_id": "171234",
  "message_id": "171235",
  "attachments": [
    {
      "id": "att-1",
      "kind": "document",
      "filename": "example.docx",
      "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "staged_path": "/workspace/message/slack-C123-171234-171235/source/example.docx",
      "format_hint": "docx"
    },
    {
      "id": "att-2",
      "kind": "image",
      "filename": "diagram.png",
      "staged_path": "/workspace/message/slack-C123-171234-171235/source/diagram.png",
      "content_type": "image/png"
    }
  ]
}
```

### Notes

- use one flat `attachments` array rather than separate top-level buckets
- `kind` is the discriminator
- `staged_path` is always an absolute path inside the container

## Master-Side Changes

### 1. Slack attachment extraction

File: [src/master/slack_app.py](/workspace/repo/src/master/slack_app.py)

Add extraction for supported document attachments:

- `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `application/pdf`
- filename fallback for `.docx` and `.pdf`

Behavior:

- image extraction remains
- document attachments are collected separately and forwarded to the router

### 2. Discord attachment extraction

File: [src/master/discord_app.py](/workspace/repo/src/master/discord_app.py)

Change current behavior:

- keep inline expansion only for plain text attachments
- treat `docx` and `pdf` as staged document attachments
- keep image attachment extraction

Add helpers:

- `_is_document_attachment()`
- `_extract_document_attachments()`

### 3. Router attachment normalization and staging

File: [src/master/router.py](/workspace/repo/src/master/router.py)

Add a new normalized dataclass:

```python
@dataclass(frozen=True)
class RoutedAttachment:
    id: str
    kind: str
    filename: str
    content_type: str
    source_url: str
    format_hint: str | None = None
```

Add staging functions:

- `_stage_request_attachments(...)`
- `_stage_slack_attachment(...)`
- `_stage_discord_attachment(...)`
- `_write_request_manifest(...)`
- `_cleanup_request_attachments(...)`

### 4. Dispatcher env injection

File: [src/master/router.py](/workspace/repo/src/master/router.py)

Extend `PodmanExecDispatcher.send_prompt()` to inject:

- `AGENT_REQUEST_MANIFEST=/workspace/message/<request-id>/manifest.json`
- `AGENT_REQUEST_ID=<request-id>`

This should happen on the `podman exec` call, just like current per-dispatch env vars.

### 4a. Runtime mount wiring

Files:

- [src/master/service.py](/workspace/repo/src/master/service.py)
- [src/master/runtime_adapter.py](/workspace/repo/src/master/runtime_adapter.py)

At agent start time, mount a dedicated request-storage path into the container, for example:

- host/volume -> `/workspace/message`

Master owns this storage area and writes request-scoped subdirectories into it during dispatch.

### 5. Prompt handling

Do not append document instructions to the prompt body.

The prompt should remain the user text.

The agent is expected to discover staged attachments via:

- `AGENT_REQUEST_MANIFEST`
- repo-level instructions in `AGENTS.md` / `.claude/CLAUDE.md`

## Agent-Side Changes

### 1. Worker preparation

File: [src/agent/worker.py](/workspace/repo/src/agent/worker.py)

Add a new startup stage:

- `message_storage_prepare`

Responsibilities:

- ensure `/workspace/message` exists
- ensure it is writable
- emit status events about message storage readiness

This stage should not create per-request directories. Those are master-owned at dispatch time inside the mounted request-storage area.

### 2. Request manifest reader

New file:

- `src/agent/request_manifest.py`

Responsibilities:

- load `AGENT_REQUEST_MANIFEST`
- validate schema
- expose typed accessors for attachments

Suggested interface:

```python
def load_request_manifest() -> RequestManifest: ...
```

### 3. Document ingestion CLI

New file:

- `src/agent/document_cli.py`

Command:

```bash
agent-doc ingest <staged-path> [--output-dir <dir>]
```

Required JSON output:

```json
{
  "ok": true,
  "format": "docx",
  "source_path": "/workspace/message/req-123/source/example.docx",
  "derived_markdown_path": "/workspace/message/req-123/derived/att-1/document.md",
  "assets_dir": "/workspace/message/req-123/derived/att-1/assets",
  "warnings": []
}
```

### 4. `docx` ingestion

New file:

- `src/agent/doc_ingest_docx.py`

Responsibilities:

- call Mammoth on the staged `docx`
- emit Markdown
- extract images into `assets/`
- emit an attachment-local derived manifest

### 5. `pdf` ingestion

New file:

- `src/agent/doc_ingest_pdf.py`

Responsibilities:

- call PyMuPDF4LLM / PyMuPDF on the staged `pdf`
- emit Markdown
- extract images into `assets/`
- emit an attachment-local derived manifest

### 6. Attachment-local derived manifest

For each document attachment, emit:

```json
{
  "attachment_id": "att-1",
  "source_path": "/workspace/message/req-123/source/example.docx",
  "format": "docx",
  "converter": "mammoth",
  "derived_markdown_path": "/workspace/message/req-123/derived/att-1/document.md",
  "assets": [
    "assets/image-001.png"
  ],
  "warnings": []
}
```

## Agent Workflow Contract

Repo instructions should direct both `codex` and `claude-code` to:

1. check `AGENT_REQUEST_MANIFEST`
2. ingest any document attachments before attempting analysis
3. read and edit the derived Markdown, not the original binary file
4. decide project-level source retention behavior independently

## Expected Runtime Flow

### Read-only request

1. user uploads `docx` or `pdf`
2. master stages file into `/workspace/message/<request-id>/source/`
3. master writes manifest
4. master dispatches prompt with `AGENT_REQUEST_MANIFEST`
5. agent ingests the file to Markdown
6. agent reads Markdown and replies in chat
7. master cleans up `/workspace/message/<request-id>/`

### Modify-and-commit request

1. user uploads `docx` or `pdf`
2. same staging and ingestion flow
3. agent edits derived Markdown
4. agent copies or writes the final durable Markdown artifact into `/workspace/repo/...`
5. agent commits and pushes
6. agent replies with URL
7. master cleans up `/workspace/message/<request-id>/`

## Important Boundary

Derived request artifacts may initially live under `/workspace/message/...`, but anything that must be committed must end up inside `/workspace/repo/...`.

That copy/move step is part of the agent task workflow, not part of master routing.

## Logging and Observability

Add structured logs for:

- attachment detection
- attachment download success/failure
- request manifest write
- env injection path
- request cleanup start/finish
- agent ingest success/failure
- derived artifact locations

## Validation Rules

Master should reject:

- unsupported document types
- attachments above a configurable size threshold
- missing download URLs

Agent ingest should fail clearly on:

- unreadable manifest
- missing staged file
- unsupported format
- converter failure

## Tests

### Master unit tests

- Slack document attachment extraction
- Discord document attachment extraction
- request id generation
- manifest JSON generation
- per-dispatch env injection

### Router tests

- mixed image + document attachment staging
- no prompt augmentation for documents
- correct `AGENT_REQUEST_MANIFEST` propagation

### Agent unit tests

- request manifest loading
- `docx` ingest command output shape
- `pdf` ingest command output shape

### Integration tests

- staged `docx` flows from master to agent and produces derived Markdown
- staged `pdf` flows from master to agent and produces derived Markdown
- modification flow can place durable output into repo and commit it

## Delivery Plan

### Phase 1: Master attachment plumbing

- add document-attachment extraction to Slack and Discord frontends
- add request id generation
- add request-scoped staging and manifest writing
- add request-storage mount wiring on agent start
- inject `AGENT_REQUEST_MANIFEST`

### Phase 2: Agent ingestion foundation

- add request manifest loader
- add `agent-doc ingest`
- implement `docx` ingestion with Mammoth
- implement `pdf` ingestion with PyMuPDF4LLM

### Phase 3: Agent workflow integration

- update repo instructions for attachment discovery
- add durable output workflow for commit-ready Markdown
- add tests for both `codex` and `claude-code` execution paths where feasible

### Phase 4: Hardening

- file size limits
- cleanup policy for `/workspace/message/<request-id>/`
- converter warning surfacing
- unsupported feature reporting for tables/images/layout issues

## Non-Goals

- `.doc` support in v1
- regenerating `docx` or `pdf`
- OCR-heavy scanned PDF support
- perfect binary round-trip fidelity
