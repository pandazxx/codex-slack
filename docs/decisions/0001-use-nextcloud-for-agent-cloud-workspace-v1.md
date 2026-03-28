---
title: "ADR-0001: Use Nextcloud for agent cloud workspace v1"
status: accepted
date: 2026-03-28
decision-makers: [project maintainers]
consulted: [architecture review completed]
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
4. Synology Drive + Synology Office + Synology Drive API
5. Dropbox + Microsoft Office web/mobile integrations + Dropbox API

## Decision Outcome

*Chosen option:* Option 1 — Nextcloud + Nextcloud Office + WebDAV — because it best satisfies the decision drivers with predictable file semantics for agents, a strong browser GUI for users, and a straightforward auth and CRUD model that the master can control.

### Consequences

- *Good:* Agents can work against a normal file tree instead of provider-specific document abstractions.
- *Good:* Users get a web UI plus browser editing support for common office files.
- *Good:* WebDAV gives a stable first integration point and keeps the backend abstraction simple.
- *Good:* The design can stay backend-agnostic so later support for OneDrive or Google Drive is still possible.
- *Bad:* The project must operate or depend on a Nextcloud deployment.
- *Bad:* v1 should use on-demand remote CRUD with local temporary working copies, so request latency and file-lifecycle cleanup must be handled explicitly.
- *Bad:* Office editing parity depends on the Nextcloud Office deployment and configured app support.

### Confirmation

We will consider this decision validated when:
- master can provision an agent with cloud workspace settings and scoped credentials
- agent startup can pull a remote cloud workspace into a local working directory
- agent can modify and upload files back to the remote workspace
- at least one end-to-end test path covers document fetch, edit, and writeback for representative office-compatible files
- architecture review accepts this ADR

## Pros and Cons of the Options

### Option 1: Nextcloud + Nextcloud Office + WebDAV

Self-hosted cloud file platform with browser UI, office integrations, and filesystem-like remote access.

- Pro: Strong match for file/folder semantics that agents already expect.
- Pro: Friendly browser GUI for users to manage workspace files.
- Pro: Good fit for master-managed auth cascaded to agents through scoped credentials.
- Pro: WebDAV supports a straightforward on-demand fetch and writeback architecture for v1.
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

### Option 4: Synology Drive + Synology Office + Synology Drive API

Private-cloud file platform on Synology NAS with browser access, file sync, and built-in office collaboration.

- Pro: Strong self-hosted story for teams that want full data ownership on their own NAS.
- Pro: Synology Drive provides a web portal, desktop sync clients, and mobile access.
- Pro: Synology Office supports browser-based documents, spreadsheets, and slides with import/export support.
- Pro: A Synology Drive API exists, so integration is possible without screen automation.
- Con: It is better framed as a NAS-centric platform than a generic cloud backend, which narrows the deployment audience.
- Con: The agent integration would likely need to target Synology-specific APIs and operational assumptions rather than a broadly portable protocol-first model.
- Con: It is a stronger fit for organizations that already run Synology infrastructure than for a default first backend.

### Option 5: Dropbox + Microsoft Office web/mobile integrations + Dropbox API

Managed cloud storage with a strong user-facing file UI, API support, and Office editing through Dropbox and Microsoft integrations.

- Pro: Very familiar managed-cloud user experience with low setup friction.
- Pro: Dropbox has a mature developer platform for file operations and auth.
- Pro: Users can edit Microsoft Office files from Dropbox using browser and mobile integrations.
- Con: Office editing relies on Microsoft integrations rather than a single native document model owned by the storage platform.
- Con: Some document workflows create links or provider-specific shortcuts instead of clean filesystem-like files.
- Con: The resulting workspace semantics are less predictable for agent automation than a WebDAV-style remote tree or a fully self-hosted file platform.

## Implementation Notes

This ADR records the current recommendation for v1 only:

- Keep the feature backend-agnostic in code, but implement Nextcloud first.
- Prefer on-demand CRUD with temporary local working files over a persistent mirror or live FUSE-style mount in v1.
- Let master own primary authentication and pass scoped access details to the agent.
- Use a dedicated temporary working area in the agent such as `/workspace/cloud-tmp` while a document operation is in flight.
- Treat office-file parsing and editing as an agent-local capability, not a cloud-provider capability, with provider-native document APIs added only as optional later adapters.

## Resolved Follow-Up Decisions

### 1. Deployment scope for v1

Decision: keep this open for further elaboration.

Rationale:

- The support boundary between self-hosted, admin-controlled hosted, and third-party managed Nextcloud still needs a more precise support matrix.
- The current decision is sufficient to proceed with architecture and implementation work, but not yet precise enough for the release promise.

### 2. Sync timing for v1

Decision: do not maintain a mirrored workspace. Use CRUD on demand.

The workflow should be:

- fetch the remote file only when an operation requires it
- operate on a temporary local working file
- write the updated file back immediately after successful validation
- clean up temporary local state after completion

Rationale:

- This removes the need for background synchronization logic.
- It keeps the runtime simpler and more request-driven.
- It avoids drift between long-lived local state and the remote source.

### 3. Conflict policy for v1

Decision: there should be no persistent sync-conflict workflow in v1 because the system will not maintain a mirrored workspace.

The workflow should be:

- fetch the current remote file when the operation begins
- validate and write back immediately after the edit
- avoid long-lived local copies and background divergence

Rationale:

- The chosen CRUD-on-demand model removes the main source of sync conflicts.
- The product does not need a user-facing sync-conflict resolution flow in v1.

### 4. Office tooling baseline for v1

Decision: ship a minimal local document toolchain in the agent image:

- `python-docx` for `docx`
- `openpyxl` for `xlsx`
- `python-pptx` for `pptx`
- `pypdf` for `pdf`
- `Pillow` as a supporting image utility

Deferred from v1 baseline:

- LibreOffice headless
- OCR toolchains
- advanced document-conversion pipelines

Rationale:

- The minimal Python-first stack is sufficient for structured v1 operations.
- Heavier rendering and conversion tools should be added only when required by real workflows.

## References

- Nextcloud Office: https://nextcloud.com/office/
- Nextcloud WebDAV user docs: https://docs.nextcloud.com/server/latest/user_manual/mn/files/access_webdav.html
- Nextcloud WebDAV API basics: https://docs.nextcloud.com/server/latest/developer_manual/client_apis/WebDAV/basic.html
- Microsoft Graph OneDrive overview: https://learn.microsoft.com/en-us/graph/onedrive-concept-overview
- Microsoft web editing overview: https://support.microsoft.com/en-us/office/using-office-for-the-web-in-onedrive-dc62cfd4-120f-4dc8-b3a6-7aec6c26b55d
- Google Drive file model overview: https://developers.google.com/workspace/drive/api/guides/about-files
- Google Drive upload guide: https://developers.google.com/workspace/drive/api/guides/manage-uploads
- Google Workspace Office-file support: https://support.google.com/docs/answer/9406611?hl=en
- Synology Drive overview: https://www.synology.com/en-us/dsm/feature/drive
- Synology Office overview: https://www.synology.com/en-global/dsm/feature/office
- Synology Drive Server help: https://kb.synology.com/api/v1/findHelpFile/dsm/SynologyDrive/2.0/enu/6.2-24922/synology_apollolake_218%2B/100/drive_desc.html
- Dropbox developer platform: https://www.dropbox.com/developers/documentation
- Dropbox collaborative Microsoft Office editing: https://help.dropbox.com/view-edit/collaborate-on-microsoft-office
- Dropbox Office integration FAQ: https://help.dropbox.com/integrations/microsoft-office-faq
- Office file handling analysis: `docs/design/cloud-workspace-office-file-analysis.md`
- File handling design discussion: `docs/design/cloud-workspace-file-handling.md`
