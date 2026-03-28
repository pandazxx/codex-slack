# Cloud Workspace File Handling Design

**Status:** draft
**Author:** Codex architect
**Date:** 2026-03-28
**Related ADRs:** ADR-0001, ADR-0002

## Problem Statement

ADR-0001 settles the storage backend choice: `Nextcloud` is the v1 cloud workspace backend. The next design question is how an agent should actually read and write office files in that workspace, including text, tables, and embedded images where possible.

The key architecture choice is whether office-file handling should be implemented as:

- a cascaded MCP-style remote tool layer
- a skill-driven prompt workflow
- or a first-class local runtime capability inside the agent environment

## Recommendation

Recommendation: implement office-file handling as a **first-class local agent capability**, not as a cascaded MCP dependency and not as a skill.

Use skills only as thin orchestration layers that tell the agent when and how to use the document tools. If MCP is introduced later, use it only for specialized augmentations such as OCR, preview rendering, or external document intelligence services.

## Why Not a Skill?

Skills are instruction layers. They are good at:

- choosing workflows
- prompting for clarification
- standardizing output formats
- coordinating multiple tools

Skills are not the right primitive for:

- parsing binary office formats
- preserving document structure while editing
- extracting images from package formats
- updating spreadsheets and presentations safely

A skill can tell the agent to read a workbook and update a table, but the actual reliable work still needs concrete document-processing code and libraries underneath it.

## Why Not Cascaded MCP as the Core Path?

Using a cascaded MCP-style service as the main file-processing path looks attractive at first because it centralizes tooling, but it is the wrong default for this project.

### Drawbacks

- It adds another runtime dependency for every agent task.
- It forces large or complex document payloads through a tool boundary.
- It makes local sync less useful, even though local sync is already part of the storage design.
- It creates a second failure domain on top of Nextcloud sync.
- It makes provider-independent document handling harder, not easier.

### Where MCP Could Help Later

MCP is still useful for optional specialized services:

- OCR for scanned PDFs and screenshots
- document preview rendering
- advanced table extraction from difficult PDFs
- malware scanning or DLP checks
- policy-enforced transformations owned by another system

That is an augmentation story, not the primary editing path.

## Proposed Architecture

### Layer 1: Cloud Sync

Master and agent coordinate to mirror a Nextcloud workspace into a local directory such as `/workspace/cloud`.

This layer owns:

- authentication
- download and upload
- sync policy
- conflict detection

This layer does **not** parse office file internals.

### Layer 2: Document Toolchain in the Agent Image

The agent image should include document-processing libraries and a small internal document API.

The internal API should expose capability-oriented operations, for example:

- `inspect_document(path)`
- `extract_text(path)`
- `extract_tables(path)`
- `extract_images(path)`
- `apply_text_updates(path, patch_spec)`
- `replace_table(path, locator, table_data)`
- `insert_image(path, locator, image_path)`

This layer is where actual file parsing and writing happens.

### Layer 3: Agent Workflow Logic

The agent decides when to:

- inspect a document
- summarize structure for the user
- ask for confirmation before destructive edits
- apply updates
- sync results back

This is where a skill can help, but the skill remains an orchestration aid rather than the document engine itself.

## Format-by-Format Capability Target

### `docx`

Target support in v1:

- read paragraphs
- read and update tables
- extract inline images where available
- insert or replace simple text blocks
- insert or replace simple images

Notes:

- Good fit for local parsing and writing
- Do not promise full fidelity for tracked changes, comments, or floating layout objects

### `xlsx`

Target support in v1:

- read sheets, ranges, formulas, and tables
- append and update tabular data
- inspect and manipulate some embedded images

Notes:

- Strong local automation case
- Avoid promising safe support for macros, pivot tables, and complex chart-heavy workbooks in v1

### `pptx`

Target support in v1:

- read slide text
- read and update simple table shapes
- read, add, and replace pictures
- update speaker-facing content in basic slide layouts

Notes:

- Reasonable local automation path
- Do not promise animation, SmartArt, or advanced design fidelity

### `pdf`

Target support in v1:

- extract machine-readable text
- extract embedded images where possible
- attempt table extraction on a best-effort basis
- generate replacement PDFs from structured output if needed

Notes:

- PDF should be treated as a weak editing format
- For complex updates, generate a new PDF from a structured source instead of mutating layout-heavy content directly

## Design Decision: Local Toolchain First

The strongest design for this project is:

1. Sync from Nextcloud into the local workspace.
2. Let the agent operate on local files.
3. Persist edits locally.
4. Sync back to Nextcloud.

This keeps the system coherent:

- storage is one concern
- file processing is a separate concern
- prompting and workflow are a third concern

Those concerns should not be collapsed into one skill or one remote tool server.

## Should We Use Provider-Native Office APIs?

Only selectively.

### Google

If the project later supports Google-native Docs, Sheets, and Slides, dedicated adapters may be worthwhile because Google provides strong document-object APIs.

### Microsoft

Microsoft Graph is especially relevant for Excel workbooks and may be worth a dedicated adapter later.

### Nextcloud

Nextcloud should remain a storage and sync layer from the agent’s perspective. Browser-based office editing is useful for users, but it should not define the agent’s core file-processing strategy.

## Proposed v1 Shape

### Base capability

Implement a local `document_toolkit` module in the agent runtime with per-format adapters:

- `docx_adapter`
- `xlsx_adapter`
- `pptx_adapter`
- `pdf_adapter`

Each adapter should expose a narrow structured interface rather than raw library objects.

### Optional skill

Add a skill later only if we want a repeatable document workflow, for example:

- inspect file structure first
- summarize editable regions
- ask before replacing tables or images
- produce a change report after edits

That skill would improve consistency, but it would not own parsing or writing.

### Optional MCP later

Consider MCP only for:

- OCR
- preview rendering
- specialized PDF extraction
- enterprise policy services

Do not require MCP for ordinary `docx`, `xlsx`, `pptx`, or simple `pdf` processing.

## Open Questions

- Should the document toolkit live under `src/agent/` or a shared package used by both master and agent utilities?
- Do we want a single normalized intermediate representation for text, tables, and images across all formats?
- Should the first implementation expose document operations as Python library calls only, or also as internal CLI commands for debugging?
- What is the minimum confidence threshold before the agent is allowed to overwrite a user document automatically?

## Decision Summary

For file handling, the project should choose:

- `Nextcloud` for storage and sync
- local agent libraries for document parsing and editing
- skills for workflow guidance only
- optional MCP later for specialized augmentations, not the default path
