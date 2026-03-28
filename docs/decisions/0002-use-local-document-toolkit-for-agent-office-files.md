---
title: "ADR-0002: Use a local document toolkit for agent office file handling"
status: accepted
date: 2026-03-28
decision-makers: [project maintainers]
consulted: [architecture review completed]
informed: [master and agent operators]
---

## Context and Problem Statement

ADR-0001 establishes `Nextcloud` as the v1 cloud workspace backend. The remaining architectural question is how agents should read and write office files fetched on demand from that workspace, including text, tables, and embedded images where feasible.

The main options are:

- treat office handling as a first-class local agent capability
- route office handling through a cascaded MCP-style service
- treat office handling as a skill-driven prompt workflow

The project runs in a headless container-managed environment, so the chosen approach must remain reliable when agents operate on temporary local working copies without interactive desktop tooling.

## Decision Drivers

- Reliable handling of `docx`, `xlsx`, `pptx`, and `pdf` in a headless runtime
- Ability to read and update text, tables, and embedded images where realistically possible
- Provider independence once files have been fetched locally
- Low runtime complexity for v1
- Clear separation between storage/CRUD concerns and document-processing concerns
- Extensibility for future OCR, preview, and document-intelligence services

## Considered Options

1. Local document toolkit inside the agent runtime
2. Cascaded MCP service as the primary document-processing path
3. Skill-driven office workflow as the primary implementation

## Decision Outcome

*Chosen option:* Option 1 — local document toolkit inside the agent runtime — because office-file parsing and writing are core execution capabilities, not orchestration concerns, and they are more reliable when performed directly against local temporary working files.

### Consequences

- *Good:* Document handling stays provider-agnostic after fetch.
- *Good:* Agents can operate on local files without depending on another remote tool service.
- *Good:* The design maps cleanly to `docx`, `xlsx`, `pptx`, and limited `pdf` support.
- *Good:* Skills can still improve workflow consistency without owning binary file parsing.
- *Bad:* The agent image must carry document-processing libraries and related maintenance burden.
- *Bad:* Advanced use cases such as OCR-heavy PDFs and high-fidelity rendering still need later specialized tooling.
- *Bad:* Complex Office features will still have format-specific fidelity limits in v1.

### Confirmation

We will consider this decision validated when:

- the agent runtime exposes a local document toolkit with per-format adapters
- the toolkit can extract text and tables from representative `docx`, `xlsx`, and `pptx` files
- the toolkit can extract images where supported by the underlying format libraries
- the toolkit can apply safe write/update operations for basic document edits
- the cloud workspace flow can fetch documents on demand, let the agent modify temporary local copies, and write them back immediately

## Pros and Cons of the Options

### Option 1: Local document toolkit inside the agent runtime

Use local libraries and internal adapters to inspect and update files in temporary local working copies.

- Pro: Best fit for headless container execution.
- Pro: Works directly on local files after Nextcloud fetch.
- Pro: Keeps storage and document processing cleanly separated.
- Pro: Avoids passing large binary payloads through a remote tool boundary.
- Con: Requires packaging and maintaining file-format libraries in the agent image.
- Con: Advanced formats still need careful capability boundaries.

### Option 2: Cascaded MCP service as the primary document-processing path

Use a remote tool server to parse and modify office files on behalf of the agent.

- Pro: Centralizes some specialized processing logic.
- Pro: Could help later for OCR, rendering, or enterprise policy integration.
- Con: Adds another runtime dependency and failure domain.
- Con: Adds another runtime dependency and failure domain.
- Con: Makes ordinary document handling more operationally complex than necessary.
- Con: Pushes provider-independent file operations through a remote boundary for little gain.

### Option 3: Skill-driven office workflow as the primary implementation

Use a prompt skill as the main mechanism for office-file handling behavior.

- Pro: Useful for standardizing user-facing workflow and confirmation steps.
- Pro: Cheap to iterate on orchestration behavior.
- Con: Skills do not parse binary file formats or preserve document structure by themselves.
- Con: Reliable text/table/image extraction still requires concrete code underneath.
- Con: It confuses orchestration with execution capability.

## Implementation Notes

This ADR records the current recommendation for v1 only:

- Put the core document-processing code inside the agent runtime.
- Expose per-format adapters for `docx`, `xlsx`, `pptx`, and `pdf`.
- Treat `pdf` support as extraction-first and best-effort for semantic edits.
- Use skills only as workflow wrappers around the document toolkit.
- Reserve MCP for optional later augmentations such as OCR, preview rendering, or advanced PDF extraction.

## Resolved Follow-Up Decisions

### 1. Shared normalized representation

Decision: use a minimal normalized read model, but keep writes adapter-specific.

The normalized model should cover:

- text blocks
- tables
- images
- anchors or edit points
- validation warnings

Writes should still flow through adapter-specific operations instead of a universal full-document rewrite format.

Rationale:

- A shared read model helps workflow portability across formats.
- Adapter-specific writes preserve format-aware safety and fidelity boundaries.

### 2. Toolkit interface shape

Decision: expose a thin internal CLI as the project-owned interface. Internal modules are implementation details, not a separate architectural surface.

The CLI exists for:

- agent invocation
- debugging
- testing
- scripting

Rationale:

- The model-facing surface should stay narrow.
- The implementation can still use internal modules without committing to a dual public interface.

### 3. Confidence checks before writeback

Decision: automatic writeback is allowed only when all of the following are true:

- adapter validation passes after save
- the requested operation is explicitly supported by the adapter capability set
- the remote file can be written back immediately through the CRUD layer
- no critical unsupported element was encountered during the edit
- the agent can produce a structured change summary

If any of these fail, writeback must stop and the agent must report the reason.

Rationale:

- This creates a deterministic safety gate for document edits.
- It prevents silent corruption or blind overwrite behavior.

### 4. Explicitly unsupported v1 features

Decision: the following are out of scope for v1:

- `docx`: tracked changes, comments, floating-layout objects, complex template-preserving rewrites
- `xlsx`: macros, pivot tables, external data connections, high-fidelity chart manipulation
- `pptx`: SmartArt, animations, embedded media, high-fidelity theme/master preservation
- `pdf`: OCR-only documents, guaranteed semantic table extraction, in-place layout-faithful rewriting

Rationale:

- These features have weak reliability characteristics in a lightweight headless toolchain.
- They should not be implied by a v1 claim of "office-file support."

## References

- File handling design discussion: `docs/design/cloud-workspace-file-handling.md`
- Office file handling analysis: `docs/design/cloud-workspace-office-file-analysis.md`
- ADR-0001: `docs/decisions/0001-use-nextcloud-for-agent-cloud-workspace-v1.md`
