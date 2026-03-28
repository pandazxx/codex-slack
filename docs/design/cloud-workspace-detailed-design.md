# Design: Cloud Workspace Detailed Design

**Status:** draft
**Author:** Codex architect
**Date:** 2026-03-28
**Related ADRs:** ADR-0001, ADR-0002

## Problem Statement

ADR-0001 selects `Nextcloud` as the v1 cloud workspace backend. ADR-0002 selects a local document toolkit as the office-file handling model. This document turns those decisions into a concrete design grounded in the current codebase:

- [service.py](/workspace/repo/src/master/service.py)
- [registry.py](/workspace/repo/src/master/registry.py)
- [runtime_adapter.py](/workspace/repo/src/master/runtime_adapter.py)
- [config.py](/workspace/repo/src/master/config.py)
- [command_dispatch.py](/workspace/repo/src/master/command_dispatch.py)
- [worker.py](/workspace/repo/src/agent/worker.py)

## Remaining Clarification

One non-blocking question remains open:

- exact release support matrix for Nextcloud deployment types

This does not block the detailed design because the technical integration surface is the same as long as the target deployment exposes the required Nextcloud/WebDAV and authentication features.

## Design Goals

- Add optional Nextcloud-backed cloud workspace support per agent
- Keep master as the authentication broker and policy owner
- Use on-demand CRUD, not mirrored workspace sync
- Keep office-file parsing and editing inside the agent runtime
- Expose a narrow project-owned CLI for document operations
- Minimize disruption to the existing master/agent lifecycle

## Non-Goals

- Full live mount semantics inside the agent container
- Background synchronization
- Generic multi-provider implementation in v1
- OCR, rendering, or rich preview infrastructure in v1

## Current Codebase Fit

The existing structure already provides the right insertion points:

- [service.py](/workspace/repo/src/master/service.py): owns agent load/start lifecycle and env/mount construction
- [registry.py](/workspace/repo/src/master/registry.py): persists per-agent metadata
- [runtime_adapter.py](/workspace/repo/src/master/runtime_adapter.py): owns container creation and mounted paths
- [config.py](/workspace/repo/src/master/config.py): owns master settings and environment variables
- [worker.py](/workspace/repo/src/agent/worker.py): owns agent initialization stages

That means this feature should be implemented as:

- registry additions in master
- secret/env injection in master runtime setup
- an agent-local cloud/document toolkit
- new master commands to configure cloud workspace state

## Proposed Module Layout

### Master

- `src/master/config.py`
  - add Nextcloud-related master settings
- `src/master/registry.py`
  - persist cloud workspace configuration per agent
- `src/master/service.py`
  - add service methods to configure and inspect cloud workspace settings
  - inject cloud workspace env and secret mounts at agent start
- `src/master/command_dispatch.py`
  - add parsing and dispatch for cloud workspace commands

### Agent

- `src/agent/cloud_workspace.py`
  - resolve `nextcloud:/...` URIs
  - fetch remote files into temporary local paths
  - write files back to remote paths
- `src/agent/document_registry.py`
  - registry and selection logic for file adapters
- `src/agent/document_adapters/docx_adapter.py`
- `src/agent/document_adapters/xlsx_adapter.py`
- `src/agent/document_adapters/pptx_adapter.py`
- `src/agent/document_adapters/pdf_adapter.py`
- `src/agent/document_cli.py`
  - model-facing CLI for inspection and edits
- `src/agent/worker.py`
  - prepare cloud temp directories and secret file paths during startup

## Registry Model

The current `AgentRecord` is flat and JSON-backed. For v1, the cleanest extension is a single nested cloud-workspace field:

```python
cloud_workspace: dict[str, Any] | None = None
```

Expected shape:

```json
{
  "backend": "nextcloud",
  "enabled": true,
  "remote_root": "/my document",
  "auth_ref": "nextcloud:alice",
  "temp_root": "/workspace/cloud-tmp"
}
```

### Why a nested field

- avoids exploding `AgentRecord` with many flat fields
- preserves room for future providers
- matches the fact that cloud workspace is optional and provider-specific

## Master Settings

Add these settings in [config.py](/workspace/repo/src/master/config.py):

- `MASTER_NEXTCLOUD_BASE_URL`
- `MASTER_CLOUD_AUTH_STORE_PATH`
- `MASTER_CLOUD_TEMP_ROOT`

Recommended defaults:

- `MASTER_CLOUD_AUTH_STORE_PATH=data/master/cloud-auth.json`
- `MASTER_CLOUD_TEMP_ROOT=/workspace/cloud-tmp`

### Purpose

- `MASTER_NEXTCLOUD_BASE_URL`: default Nextcloud endpoint used by auth references that do not override host
- `MASTER_CLOUD_AUTH_STORE_PATH`: encrypted or otherwise protected auth reference storage
- `MASTER_CLOUD_TEMP_ROOT`: default temp root passed into agent runtime

## Master Auth Model

Master remains the auth broker.

### Auth store

Master should maintain a small auth-reference store keyed by `auth_ref`.

Example:

```json
{
  "nextcloud:alice": {
    "backend": "nextcloud",
    "base_url": "https://nextcloud.example.com",
    "username": "alice",
    "app_password": "..."
  }
}
```

### Design rule

- master stores the long-lived credential material
- agent receives only the specific credential file needed for its current cloud workspace access

For v1, app-password-backed Nextcloud auth is sufficient.

## Master Commands

Add explicit commands rather than overloading `/master-agent-load`.

Recommended commands:

- `/master-agent-cloud-set <name> nextcloud <remote_root> <auth_ref>`
- `/master-agent-cloud-clear <name>`
- `/master-agent-cloud-status <name>`

### Behavior

- `cloud-set`
  - validates agent exists
  - validates backend is supported
  - validates `auth_ref` exists in the auth store
  - persists cloud workspace configuration to the registry

- `cloud-clear`
  - removes the cloud workspace configuration from the registry

- `cloud-status`
  - reports backend, remote root, enabled state, and auth reference id
  - never prints the secret value

## Runtime Injection

At agent start, [service.py](/workspace/repo/src/master/service.py) should:

1. read `record.cloud_workspace`
2. resolve `auth_ref` from the master auth store
3. materialize a per-agent temporary auth file
4. mount that file read-only into the container
5. pass cloud workspace env vars into the container

### Proposed env vars

- `AGENT_CLOUD_WORKSPACE_ENABLED=true|false`
- `AGENT_CLOUD_WORKSPACE_BACKEND=nextcloud`
- `AGENT_CLOUD_WORKSPACE_REMOTE_ROOT=/my document`
- `AGENT_CLOUD_WORKSPACE_TEMP_ROOT=/workspace/cloud-tmp`
- `AGENT_CLOUD_WORKSPACE_AUTH_FILE=/run/secrets/cloud_workspace_auth.json`
- `AGENT_CLOUD_WORKSPACE_BASE_URL=https://nextcloud.example.com`

### Proposed mount

- `/run/secrets/cloud_workspace_auth.json:ro`

## Agent Startup Changes

[worker.py](/workspace/repo/src/agent/worker.py) should not fetch remote files on startup.

It should only:

- create the temp root directory
- validate that the cloud auth file exists if cloud workspace is enabled
- record cloud workspace readiness in status output

Recommended new stage:

- `cloud_workspace_prepare`

Placement:

- after `workspace_prepare`
- before `ready`

## Agent Cloud Workspace Layer

`src/agent/cloud_workspace.py` should provide:

- `resolve_uri(uri) -> RemoteRef`
- `fetch(remote_ref) -> LocalWorkFile`
- `write_back(local_work_file, remote_ref) -> WriteResult`
- `cleanup(local_work_file) -> None`

### `RemoteRef`

Suggested fields:

- `backend`
- `base_url`
- `remote_path`
- `auth_ref`

### `LocalWorkFile`

Suggested fields:

- `local_path`
- `source_uri`
- `remote_ref`
- `mime_type`

### v1 behavior

- fetch remote file to a temp path
- operate only on that temp path
- write back immediately on successful edit
- remove temp file after completion

## Document Toolkit

`src/agent/document_registry.py` should own explicit adapter registration.

### Registered adapters

- `docx_adapter`
- `xlsx_adapter`
- `pptx_adapter`
- `pdf_adapter`

### Each adapter must declare

- supported extensions
- supported MIME types
- optional probe function
- capabilities
- open/validate/save behavior

## Model-Facing CLI

Expose a single project-owned CLI entrypoint:

- `agent-doc`

Recommended commands:

- `agent-doc capabilities <path>`
- `agent-doc describe <path>`
- `agent-doc extract-images <path> --out-dir <dir>`
- `agent-doc apply-edit <path> --spec <json>`
- `agent-doc validate <path>`

### Why CLI

- stable interface for `codex` and `claude-code`
- easy to test
- easy to log
- keeps parser routing inside project code

## Request Flow

Example request:

`Read the images of nextcloud:/my document/example.docx and add a description at the bottom of each image`

### Flow

1. agent resolves `nextcloud:/my document/example.docx`
2. cloud workspace layer fetches it to `/workspace/cloud-tmp/<request-id>/example.docx`
3. agent runs `agent-doc capabilities` on that local path
4. runtime selects `docx_adapter`
5. agent runs `agent-doc extract-images` and any structure-inspection command needed
6. agent generates descriptions
7. agent runs `agent-doc apply-edit` with a structured spec targeting image anchors
8. agent runs `agent-doc validate`
9. cloud workspace layer writes the file back to the original Nextcloud path
10. temp working directory is deleted

## Edit Specification

`agent-doc apply-edit` should accept a structured JSON edit spec, not free-form instructions.

Example shape:

```json
{
  "operations": [
    {
      "op": "insert_paragraph_after_anchor",
      "anchor": "image:2",
      "text": "Description: ..."
    }
  ]
}
```

This keeps the model-facing step auditable and format-agnostic.

## Error Handling

### Fetch errors

- invalid URI
- auth failure
- file not found
- unsupported backend

### Document errors

- unsupported file format
- corrupted file
- unsupported edit operation
- validation failure after write

### Writeback errors

- remote write failure
- permission denied
- request timeout

The agent should surface these as explicit failure states, not silent fallbacks.

## Observability

Add structured events for:

- URI resolution
- remote fetch start/finish
- adapter selection
- extract/edit/validate start/finish
- writeback start/finish
- cleanup completion

These should follow the same style used by [worker.py](/workspace/repo/src/agent/worker.py) status events.

## Security Notes

- never print cloud credentials in command output
- mount auth material read-only
- clean up temporary local copies after request completion
- isolate temp work under a request-scoped directory
- do not allow arbitrary scheme handling beyond supported providers

## Testing Plan

### Unit

- registry serialization of `cloud_workspace`
- command parsing for cloud commands
- adapter registry selection logic
- document adapter capability reporting
- URI resolution and Nextcloud path normalization

### Integration

- master sets cloud workspace config and starts agent
- agent fetches a remote file using mounted credentials
- agent edits a representative `docx`
- agent writes the file back successfully

### Failure-path integration

- bad auth reference
- remote file not found
- corrupted `docx`
- validation failure blocks writeback

## Rollout Plan

1. Add registry and config support
2. Add master cloud workspace commands
3. Add runtime auth-file injection
4. Add agent cloud workspace fetch/writeback layer
5. Add document registry and adapters
6. Add `agent-doc` CLI
7. Add end-to-end tests

## Open Questions

- exact Nextcloud deployment support matrix for the release promise
- whether `auth_ref` is owned per human user, per agent, or both
- whether writeback should always overwrite directly or support optional versioned copies later
