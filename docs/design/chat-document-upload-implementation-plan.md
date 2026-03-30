# Chat Document Upload Implementation Design

**Status:** implemented baseline  
**Author:** Codex architect  
**Date:** 2026-03-29  
**Related ADRs:** `docs/decisions/0001-stage-and-convert-uploaded-documents-in-master.md`

## Goal

Implement support for `docx` and `pdf` uploads in Slack and Discord so that:

- master stages uploaded documents and images into request-scoped storage outside the repo
- master converts supported documents to Markdown before dispatch
- master extracts images from the original document into request-scoped derived storage
- generated Markdown refers to extracted images with correct relative paths
- agent discovers all staged and derived artifacts through `AGENT_REQUEST_MANIFEST`
- when modifications are requested, the agent can copy selected derived output into `/workspace/repo/...`, commit it, and return a GitHub URL

## Current Integration Points

The current code already provides the right hooks:

- [src/master/slack_app.py](/workspace/repo/src/master/slack_app.py)
  Handles Slack events and currently extracts image attachments.
- [src/master/discord_app.py](/workspace/repo/src/master/discord_app.py)
  Handles Discord messages and currently reads some attachment types inline.
- [src/master/router.py](/workspace/repo/src/master/router.py)
  Owns prompt dispatch and already stages some Slack image inputs.
- [src/master/service.py](/workspace/repo/src/master/service.py)
  Owns agent startup and is the right place to mount request storage.
- [src/master/runtime_adapter.py](/workspace/repo/src/master/runtime_adapter.py)
  Centralizes container mount wiring.

## High-Level Design

### Master responsibilities

- detect supported document and image attachments from Slack and Discord
- manage request-scoped storage through a mounted request area attached to the agent container
- download and stage uploaded source files
- convert supported `docx` and `pdf` files to Markdown before dispatch
- extract images from uploaded documents and store them in request-scoped derived storage
- ensure generated Markdown refers to extracted assets with correct relative paths
- write a request manifest JSON file that describes both source and derived artifacts
- inject `AGENT_REQUEST_MANIFEST` into the dispatch environment
- clean up the request directory after a successful reply
- keep the routed prompt close to the user’s original text

### Agent responsibilities

- read `AGENT_REQUEST_MANIFEST`
- read derived Markdown for document attachments
- read staged image attachments from the same manifest when present
- decide whether to produce durable repo output from the derived artifact
- write durable output into `/workspace/repo/...` when a commit-ready result is needed

## Request Storage Layout

Request-specific storage lives outside the repo, under `/workspace/message/`, and is exposed to the agent through a master-managed mount created when the agent container starts.

### Mount implementation choice

Use a shared named volume per agent for request storage.

Suggested shape:

- volume name: `agent-messages-<agent-name>`
- master mount path: `/workspace/messages/<agent-name>` as read-write
- agent mount path: `/workspace/message` as read-only

Rationale:

- avoids host-path coupling to `/var/lib/...`
- avoids remote-Podman bind-source provisioning issues
- keeps request storage container-native like the existing agent workspace volume
- preserves the intended ownership split: master writes request artifacts, agent reads them

Implementation note:

- master writes request files through its own writable volume mount path
- the manifest stores agent-visible absolute paths under `/workspace/message/...`
- master therefore needs a simple path translation between:
  - master write path: `/workspace/messages/<agent-name>/<request-id>/...`
  - agent read path: `/workspace/message/<request-id>/...`

### Layout

```text
/workspace/message/
  <request-id>/
    manifest.json
    source/
      file-001.docx
      file-002.pdf
      image-001.png
    derived/
      att-1/
        document.md
        assets/
          image-001.png
          image-002.jpeg
        derived.json
      att-2/
        document.md
        assets/
        derived.json
```

### Why this layout

- transient request data stays outside the Git worktree
- source and derived artifacts are grouped under one request id
- master can fully own artifact creation and cleanup
- agent can consume request data read-only
- document-local derived files can use stable relative references like `assets/image-001.png`

## Request ID Scheme

Use a deterministic request id per routed message.

### Slack

```text
slack-<channel-id>-<thread-ts-or-event-ts>-<event-ts>
```

### Discord

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
      "format_hint": "docx",
      "derived": {
        "markdown_path": "/workspace/message/slack-C123-171234-171235/derived/att-1/document.md",
        "assets_dir": "/workspace/message/slack-C123-171234-171235/derived/att-1/assets",
        "manifest_path": "/workspace/message/slack-C123-171234-171235/derived/att-1/derived.json",
        "converter": "mammoth",
        "warnings": []
      }
    },
    {
      "id": "att-2",
      "kind": "image",
      "filename": "diagram.png",
      "content_type": "image/png",
      "staged_path": "/workspace/message/slack-C123-171234-171235/source/diagram.png"
    }
  ]
}
```

### Notes

- use one flat `attachments` array rather than separate top-level buckets
- `kind` is the discriminator
- `staged_path` is always an absolute path inside the container
- `derived` is present only for converted document attachments

## Master-Side Changes

### 1. Slack attachment extraction

File: [src/master/slack_app.py](/workspace/repo/src/master/slack_app.py)

Add extraction for supported document attachments:

- `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `application/pdf`
- filename fallback for `.docx` and `.pdf`

Behavior:

- image extraction is normalized into the same attachment payload model as documents
- document and image attachments are forwarded together to the router

### 2. Discord attachment extraction

File: [src/master/discord_app.py](/workspace/repo/src/master/discord_app.py)

Change current behavior:

- keep inline expansion only for plain text attachments
- treat `docx` and `pdf` as staged document attachments
- treat images as staged image attachments in the same request manifest flow

Add helpers:

- `_is_document_attachment()`
- `_extract_document_attachments()`

### 3. Router attachment normalization and staging

File: [src/master/router.py](/workspace/repo/src/master/router.py)

Add a normalized dataclass:

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

Add staging and conversion functions:

- `_stage_request_attachments(...)`
- `_stage_slack_attachment(...)`
- `_stage_discord_attachment(...)`
- `_convert_staged_document(...)`
- `_write_request_manifest(...)`
- `_cleanup_request_attachments(...)`

Design rule:

- images and documents use the same request manifest and storage flow
- the current image-specific prompt augmentation path should be retired

### 4. Dispatcher env injection

File: [src/master/router.py](/workspace/repo/src/master/router.py)

Extend `PodmanExecDispatcher.send_prompt()` to inject:

- `AGENT_REQUEST_MANIFEST=/workspace/message/<request-id>/manifest.json`

This should happen on the `podman exec` call, just like current per-dispatch env vars.

### 5. Runtime mount wiring

Files:

- [src/master/service.py](/workspace/repo/src/master/service.py)
- [src/master/runtime_adapter.py](/workspace/repo/src/master/runtime_adapter.py)

At agent start time, mount the request-storage volume into the container as:

- named volume `agent-messages-<agent-name>` -> `/workspace/message:ro`

Master owns this storage area and writes request-scoped subdirectories into the same named volume through its own writable mount during dispatch.

### 6. Prompt handling

Do not append document or image attachment listings to the prompt body.

The prompt should remain the user text.

The agent is expected to discover staged attachments via:

- `AGENT_REQUEST_MANIFEST`
- repo-level instructions in `AGENTS.md` / `.claude/CLAUDE.md`

This replaces the current special-case image URL prompt augmentation model.

### 7. Document conversion service

Implemented file:

- `src/master/document_convert.py`

Responsibilities:

- convert staged `docx` into Markdown and extracted assets
- convert staged `pdf` into Markdown and best-effort extracted content
- write converted Markdown into `derived/<attachment-id>/document.md`
- extract document images into `derived/<attachment-id>/assets/`
- ensure Markdown uses relative image references rooted at the per-attachment `assets/` directory
- emit per-attachment `derived.json`

Current implementation notes:

- `docx` conversion currently uses an internal XML-based fallback converter in Python
- `pdf` conversion currently uses optional `pypdf` text extraction with a clear warning when the dependency is unavailable
- the ADR target remains `Mammoth + PyMuPDF4LLM`, but the code now ships a working baseline path instead of waiting on the final toolchain

Example per-attachment derived manifest:

```json
{
  "attachment_id": "att-1",
  "source_path": "/workspace/message/req-123/source/example.docx",
  "format": "docx",
  "converter": "docx-xml-fallback",
  "derived_markdown_path": "/workspace/message/req-123/derived/att-1/document.md",
  "assets_dir": "/workspace/message/req-123/derived/att-1/assets",
  "assets": [
    "assets/image-001.png"
  ],
  "warnings": []
}
```

## Agent-Side Changes

### 1. Request manifest reader

New file:

- `src/agent/request_manifest.py`

Responsibilities:

- load `AGENT_REQUEST_MANIFEST`
- validate schema
- expose typed accessors for staged source files, derived Markdown, and staged images

Suggested interface:

```python
def load_request_manifest() -> RequestManifest: ...
```

### 2. Workflow contract

Repo instructions should direct both `codex` and `claude-code` to:

1. check `AGENT_REQUEST_MANIFEST`
2. use `derived.markdown_path` for document attachments when present
3. use staged image paths directly for image attachments
4. avoid mutating request storage
5. copy or rewrite any durable output into `/workspace/repo/...` before commit

Default container-level instruction requirement:

- treat all content under `/workspace/message/...` as vulnerable transient input, not durable project state
- never rely on `/workspace/message/...` for future reference after the current request completes
- if durable output is needed, copy the Markdown and every referenced extracted asset into `/workspace/repo/...`
- rewrite links as needed so committed Markdown remains valid after request-storage cleanup

### 3. Optional helper CLI

If the agent needs a helper for manifest inspection, keep it thin and read-only, for example:

```bash
agent-request show
```

This helper is optional. The core contract is the manifest itself, not a mandatory CLI.

Implemented file:

- `src/agent/request_manifest.py`

## Unified Image Flow

Image attachments use the same request flow as documents:

- master stages them into `source/`
- manifest records them as `kind=image`
- agent reads staged image paths from the manifest
- no image URLs or image file paths are appended into the prompt

This retires the current special-case image prompt augmentation model.

## Expected Runtime Flow

### Read-only request

1. user uploads `docx`, `pdf`, and/or images
2. master stages files into `/workspace/message/<request-id>/source/`
3. master converts supported documents into `/workspace/message/<request-id>/derived/<attachment-id>/`
4. master writes manifest
5. master dispatches prompt with `AGENT_REQUEST_MANIFEST`
6. agent reads derived Markdown for documents and staged image paths for images
7. agent replies in chat
8. master cleans up `/workspace/messages/<agent-name>/<request-id>/`

### Modify-and-commit request

1. user uploads `docx` or `pdf`
2. same staging and conversion flow
3. agent edits the derived Markdown content conceptually, but writes the durable final artifact into `/workspace/repo/...`
4. agent commits and pushes
5. agent replies with the resulting URL
6. master cleans up `/workspace/messages/<agent-name>/<request-id>/`

## Important Boundaries

- request storage is read-only to the agent
- request storage is transport-scoped and cleaned by master
- durable output belongs in `/workspace/repo/...`, not in request storage
- final durable output placement inside `/workspace/repo/...` is owned by the project/agent workflow, not by a platform-fixed path convention

## Revised Request-Storage Runtime Contract

To make the named-volume design concrete, the runtime contract is:

- one request-storage named volume per agent: `agent-messages-<agent-name>`
- master runtime mounts that volume at `/workspace/messages/<agent-name>` with read-write access
- agent container mounts that same volume at `/workspace/message` with read-only access
- master creates and removes per-request directories inside its writable mount
- manifests always expose agent-facing absolute paths under `/workspace/message/...`

This keeps the storage model aligned with the repository's named-volume workspace approach while avoiding direct host filesystem dependencies.

## Logging and Observability

Add structured logs for:

- attachment detection
- attachment download success/failure
- conversion start/finish per attachment
- image extraction count and output directory
- request manifest write
- env injection path
- request cleanup start/finish
- cleanup retention on failure

## Cleanup Policy

Use this request-storage cleanup policy:

- clean request data immediately after a successful reply
- retain request data when the request fails
- failed request retention is for debugging and should later be paired with a bounded TTL or manual cleanup workflow

## Validation Rules

### Attachment acceptance policy

Use the following validation policy:

- best-effort acceptance if either MIME type or filename extension looks correct
- one global size cap for supported attachments
- hard rejection for unsupported or oversized document attachments

Master should reject:

- unsupported document types
- attachments above a configurable size threshold
- missing download URLs

Master conversion should fail clearly on:

- missing staged file
- unsupported format
- converter failure
- missing derived Markdown output

## Tests

### Master unit tests

- Slack document attachment extraction
- Slack image attachment extraction into normalized attachment records
- Discord document attachment extraction
- Discord image attachment extraction into normalized attachment records
- request id generation
- mixed document and image staging
- manifest JSON generation including derived metadata
- per-dispatch env injection
- document conversion output path generation
- Markdown image-reference correctness

### Router tests

- no prompt augmentation for images or documents
- correct `AGENT_REQUEST_MANIFEST` propagation
- cleanup on success and retention on failure

### Agent unit tests

- request manifest loading
- document attachment selection prefers `derived.markdown_path`
- image attachment selection reads staged image paths

### Integration tests

- staged `docx` flows from master to agent with converted Markdown and extracted images
- staged `pdf` flows from master to agent with converted Markdown and extracted images
- modification flow can place durable output into repo and commit it

## Delivery Plan

### Phase 1: Master attachment plumbing

- add document-attachment extraction to Slack and Discord frontends
- fold image attachment handling into the same normalized attachment/staging path
- add request id generation
- add request-scoped staging and manifest writing
- add request-storage mount wiring on agent start
- inject `AGENT_REQUEST_MANIFEST`

### Phase 2: Master conversion foundation

- implement `docx` conversion with Mammoth
- implement `pdf` conversion with PyMuPDF4LLM
- write per-attachment derived manifests
- validate Markdown-relative image references

### Phase 3: Agent workflow integration

- update repo instructions for manifest-driven attachment discovery
- ensure agent workflows consume derived Markdown and staged images
- add durable output workflow for commit-ready Markdown

### Phase 4: Hardening

- file size limits
- failure retention policy hardening
- converter warning surfacing
- unsupported feature reporting for tables/images/layout issues

## Non-Goals

- `.doc` support in v1
- regenerating `docx` or `pdf`
- OCR-heavy scanned PDF support
- perfect binary round-trip fidelity
