---
title: "ADR-0001: Use Nextcloud for agent cloud workspace v1"
status: proposed
date: 2026-03-28
decision-makers: [project maintainers]
consulted: [architecture review pending]
informed: [master and agent operators]
---

## Context and Problem Statement

The master/agent runtime currently gives each agent a local workspace, but it does not provide an optional user-managed cloud file workspace. We need a backend that lets users manage files through a friendly GUI, supports common office documents, allows agents to read and write those files, and keeps authentication under master control so scoped access can be cascaded to agents.

## Decision Drivers

- Optional attachment to an agent container without breaking existing local-only workflows
- Reliable agent read/write access to normal files and folders
- User-friendly browser GUI for file management
- Browser-based handling of common office documents: `docx`, `xlsx`, `pptx`, `pdf`
- Master-managed authentication with least-privilege agent access
- Low operational complexity for the first implementation
- Clear path to add more storage backends later

## Considered Options

1. Nextcloud + Nextcloud Office + WebDAV
2. Microsoft OneDrive / SharePoint + Microsoft 365 + Graph API
3. Google Drive + Google Workspace editors + Drive API

## Decision Outcome

*Chosen option:* Option 1 — Nextcloud + Nextcloud Office + WebDAV — because it best satisfies the decision drivers with predictable file semantics for agents, a strong browser GUI for users, and a straightforward auth and sync model that the master can control.

### Consequences

- *Good:* Agents can work against a normal file tree instead of provider-specific document abstractions.
- *Good:* Users get a web UI plus browser editing support for common office files.
- *Good:* WebDAV gives a stable first integration point and keeps the backend abstraction simple.
- *Good:* The design can stay backend-agnostic so later support for OneDrive or Google Drive is still possible.
- *Bad:* The project must operate or depend on a Nextcloud deployment.
- *Bad:* v1 should use local mirror plus sync, not a live remote mount, so file conflicts and sync timing must be handled explicitly.
- *Bad:* Office editing parity depends on the Nextcloud Office deployment and configured app support.

### Confirmation

We will consider this decision validated when:
- master can provision an agent with cloud workspace settings and scoped credentials
- agent startup can pull a remote cloud workspace into a local working directory
- agent can modify and upload files back to the remote workspace
- at least one end-to-end test path covers document sync for representative office-compatible files
- architecture review accepts or amends this ADR

## Pros and Cons of the Options

### Option 1: Nextcloud + Nextcloud Office + WebDAV

Self-hosted cloud file platform with browser UI, office integrations, and filesystem-like remote access.

- Pro: Strong match for file/folder semantics that agents already expect.
- Pro: Friendly browser GUI for users to manage workspace files.
- Pro: Good fit for master-managed auth cascaded to agents through scoped credentials.
- Pro: WebDAV supports a simple local-mirror sync architecture for v1.
- Con: Requires operating or adopting a Nextcloud service.
- Con: Some advanced office behavior depends on Nextcloud Office deployment quality.

### Option 2: Microsoft OneDrive / SharePoint + Microsoft 365 + Graph API

Enterprise cloud storage with strong browser editing and Microsoft-native collaboration.

- Pro: Excellent office document UX for users.
- Pro: Mature APIs and enterprise identity integration.
- Pro: Strong fit for organizations already standardized on Microsoft 365.
- Con: API model is less filesystem-like than WebDAV-backed storage.
- Con: Auth, tenancy, and admin setup are heavier for a first implementation.
- Con: Tighter vendor coupling in both product behavior and integration design.

### Option 3: Google Drive + Google Workspace editors + Drive API

Widely used cloud storage with browser editing and support for Office file import/export.

- Pro: Friendly web UI and broad user familiarity.
- Pro: Strong collaboration features in browser editors.
- Pro: Viable future backend if teams are standardized on Google Workspace.
- Con: Native Google document types are not normal files from an agent perspective.
- Con: Upload, export, and conversion behavior is less predictable for automated editing workflows.
- Con: File semantics are weaker for a workspace that should behave like a normal directory tree.

## Implementation Notes

This ADR records the current recommendation for v1 only:

- Keep the feature backend-agnostic in code, but implement Nextcloud first.
- Prefer a `local mirror + explicit sync` model over a live FUSE-style mount in v1.
- Let master own primary authentication and pass scoped access details to the agent.
- Mount the synchronized workspace into the agent as a dedicated path such as `/workspace/cloud`.

## Follow-Up Questions

- Should the first release support only self-hosted Nextcloud, or also managed deployments?
- Should sync happen only on startup and task completion, or also on explicit command?
- What conflict policy should apply if both the user and the agent modify the same file?
- Which office-file libraries and conversion tools should be included in the agent image for v1?
