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

This layer should also include an adapter registry that maps a resolved local file to the correct document adapter by:

- URI scheme
- file extension
- MIME type when available
- lightweight file-signature checks when needed

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

## Example Request Flow

Example user request:

`Read the images of nextcloud:/my document/example.docx and add a description at the bottom of each image`

The expected execution flow is:

### 1. Resolve and fetch the file

The agent should not open `nextcloud:/...` directly through a document parser.

Instead:

1. The cloud workspace layer resolves `nextcloud:/my document/example.docx` to a remote workspace path.
2. The sync layer ensures the latest remote version is present in the local mirror, for example:
   `/workspace/cloud/my document/example.docx`
3. The workflow records the local path, remote path, and file revision metadata before editing.

This keeps provider concerns at the sync layer and gives the document layer a normal local file path.

### 2. Select the correct document adapter

The document toolkit should inspect the local file and choose the adapter from a registry.

For this example:

- remote path resolves to local `.docx`
- adapter registry selects `docx_adapter`
- the adapter reports supported operations such as:
  - text extraction
  - table extraction
  - image extraction
  - paragraph insertion
  - image-adjacent content insertion

The agent should never choose tools by prompt text alone. Tool selection should be deterministic from the resolved file type and advertised adapter capabilities.

## How the Agent Actually Chooses the Correct Tool

This is a two-stage decision, and it should be designed so both `claude-code` and `codex` can succeed reliably.

### Stage A: The model chooses a high-level document operation

The model should not be asked to choose among many low-level file parsers.

Instead, expose a small number of high-level operations such as:

- `workspace.resolve_cloud_uri(uri)`
- `document.inspect(path)`
- `document.extract_content(path)`
- `document.apply_structured_edit(path, edit_spec)`
- `workspace.sync_back(path, remote_ref)`

At this stage, the model only needs to infer:

- this request is about a cloud-backed document
- it needs inspection before editing
- it needs writeback after successful validation

That is a strength of both `claude-code` and `codex`: they are good at choosing workflow steps when the tool surface is compact and semantically clear.

### Stage B: The runtime chooses the actual parser or writer

Once the model invokes the high-level document operation, normal code should choose the actual adapter.

That choice should be deterministic and based on:

1. URI scheme or storage provider
2. resolved local file path
3. file extension
4. MIME type if known
5. lightweight file-signature checks if extension and MIME disagree
6. advertised adapter capabilities

In other words:

- the model chooses `document.extract_content(...)`
- the runtime chooses `docx_adapter`, `xlsx_adapter`, `pptx_adapter`, or `pdf_adapter`

This separation is important. It avoids making the LLM responsible for technical parser routing.

### What the agent should not do

The agent should not:

- parse the filename in free-form prompt space and guess a library
- pick between ten file-format tools with overlapping descriptions
- directly manipulate binary office files through generic text-edit tools
- decide writeback rules without capability checks and validation

## Recommended Tool Surface for Claude-Code and Codex

To make tool selection reliable across both agents, the document capability should be exposed as a narrow, provider-independent interface.

### Good interface shape

- `workspace.resolve_cloud_uri(uri)`
- `workspace.sync_down(remote_ref)`
- `document.open(local_path)`
- `document.get_capabilities(local_path)`
- `document.describe_structure(local_path)`
- `document.extract_images(local_path)`
- `document.apply_edit(local_path, edit_spec)`
- `document.validate(local_path)`
- `workspace.sync_up(local_path, remote_ref, revision)`

### Bad interface shape

- `use_python_docx`
- `use_openpyxl`
- `use_python_pptx`
- `use_pypdf`
- `use_nextcloud_webdav`

The bad shape forces the model to do internal implementation routing. The good shape lets the model express intent while the runtime owns dispatch.

## Concrete Decision Logic

Using the example:

`Read the images of nextcloud:/my document/example.docx and add a description at the bottom of each image`

The actual selection sequence should be:

1. Model calls `workspace.resolve_cloud_uri("nextcloud:/my document/example.docx")`
2. Runtime returns a `remote_ref`
3. Model calls `workspace.sync_down(remote_ref)`
4. Runtime returns:
   - `local_path=/workspace/cloud/my document/example.docx`
   - `mime_type=application/vnd.openxmlformats-officedocument.wordprocessingml.document`
   - `revision=<remote revision>`
5. Model calls `document.get_capabilities(local_path)`
6. Runtime selects `docx_adapter` and returns capabilities such as:
   - `can_extract_text=true`
   - `can_extract_tables=true`
   - `can_extract_images=true`
   - `can_insert_paragraph_after_anchor=true`
   - `layout_limitations=["floating_images_may_be_unsupported"]`
7. Based on those capabilities, the model proceeds with extraction and edit calls

The important point is that the model is using tool outputs, not making hidden guesses.

## Why This Works for Both Claude-Code and Codex

Both agents are much more reliable when:

- the number of tool choices is small
- the tool names describe user intent, not implementation details
- the runtime returns explicit capability metadata
- unsupported operations fail early and clearly

If the tool surface is designed this way, the model does not need special file-format expertise to route requests correctly. It only needs to follow a stable workflow:

- resolve
- sync
- inspect capabilities
- extract
- edit
- validate
- write back

That is portable across `claude-code` and `codex`.

### 3. Read document structure, text, and images

The `docx_adapter` should expose a structured read model, for example:

- document text blocks
- tables
- image objects
- image anchors or nearest surrounding paragraphs
- element ordering in the document body

For this specific request, the agent needs:

- the ordered list of images
- a stable anchor for each image in the document flow
- enough nearby context to insert a description under each image instead of in an arbitrary location

The image bytes or extracted image files can then be passed to an image-description step if the agent needs to generate descriptions from image content rather than surrounding text.

### 4. Generate the descriptions

For each image, the agent should generate a candidate description using one or both of:

- direct image analysis
- document context near the image

This is where multimodal inference may be used, but it is separate from the office-file parser.

The output of this step should be structured, for example:

- `image_id`
- `anchor_location`
- `generated_description`

### 5. Apply the document update

The `docx_adapter` should then:

- reopen or continue holding the local document model
- insert a paragraph immediately after each image anchor, or in the nearest valid position below the image in document flow
- write the generated description into that inserted paragraph

For v1, the implementation should use conservative insertion behavior:

- operate only on inline-image flows that have reliable anchors
- skip or warn on unsupported floating-layout cases
- avoid rewriting unrelated document structure

### 6. Validate the rewritten file

Before any writeback to Nextcloud, the agent should:

- save the modified document to the local mirror
- reopen it through the same adapter
- verify that the document is still readable
- verify that the inserted descriptions appear in the expected count and positions

If validation fails, the sync layer should not upload the modified file.

### 7. Write back to the remote path

Once validation succeeds:

1. The cloud sync layer marks the local file as changed.
2. The sync layer uploads the updated file back to:
   `nextcloud:/my document/example.docx`
3. The sync layer checks for revision conflicts before overwrite, according to the workspace conflict policy.
4. The agent reports:
   - what file was modified
   - how many images were described
   - whether any images were skipped due to unsupported layout

## Required Internal Interfaces

To make the above flow reliable, the implementation should define explicit interfaces between layers.

### Cloud workspace interface

- `resolve_uri(uri) -> remote_ref`
- `sync_down(remote_ref) -> local_path, revision`
- `sync_up(local_path, remote_ref, expected_revision) -> new_revision`

### Document adapter registry

- `open_document(local_path) -> adapter_instance`
- `get_capabilities(local_path) -> capability_set`

### Document adapter interface

- `extract_text()`
- `extract_tables()`
- `extract_images()`
- `describe_edit_points()`
- `insert_paragraph_after_anchor(anchor, text)`
- `save()`
- `validate()`

### Agent workflow contract

- resolve remote path
- sync down
- open with adapter
- perform modality-specific analysis
- apply structured edits
- validate
- sync up

This contract is the reason ADR-0002 favors a local document toolkit over a skill or cascaded MCP. The workflow needs stable execution semantics, not just prompt guidance.

## How Adapters Are Found

Yes. The adapters should be registered in code.

The agent should not "discover" adapters through prompting, and it should not rely on the model remembering which parser handles which format. The runtime should own a `DocumentAdapterRegistry`.

## Recommended Registry Design

For v1, use a static built-in registry loaded at agent startup.

Example shape:

- `docx_adapter`
- `xlsx_adapter`
- `pptx_adapter`
- `pdf_adapter`

Each adapter registration should declare:

- adapter name
- supported file extensions
- supported MIME types
- optional file-signature or package checks
- read capabilities
- write capabilities
- validation function
- priority or specificity

## Selection Algorithm

When the agent has a resolved local path, the runtime should:

1. normalize the path and extension
2. determine MIME type if available
3. run lightweight file-signature checks if needed
4. ask the registry for matching adapters
5. select the most specific compatible adapter
6. return the adapter and its capabilities to the workflow

That means the selection call is something like:

- `registry.select(local_path, mime_type)`

And the result is something like:

- `adapter=docx_adapter`
- `capabilities={extract_text, extract_tables, extract_images, insert_paragraph_after_anchor, validate}`

## Why Registration Is Better Than Free-Form Discovery

Explicit registration gives us:

- deterministic routing
- testable behavior
- clear unsupported-format errors
- a stable place to add new formats later
- no dependency on model-specific prompt behavior

It also lets the runtime reject bad matches, for example:

- `.docx` extension but invalid zip package
- renamed `.pdf` that is actually an image
- `.xlsx` that is macro-heavy or corrupted and fails validation

## Static Registry vs Plugin Registry

For v1, use a static registry in the agent codebase.

That is the simplest and most reliable path because:

- the supported formats are known
- the agent image already controls the installed libraries
- startup behavior is predictable
- testing is straightforward

Later, if the project needs third-party or provider-specific adapters, the registry can evolve into a plugin model. But that should be a later extension, not the first implementation.

## Example Registration Shape

The design should look roughly like this:

```python
DocumentAdapterRegistry.register(
    name="docx_adapter",
    extensions=[".docx"],
    mime_types=[
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
    probe=probe_docx,
    capabilities={
        "extract_text",
        "extract_tables",
        "extract_images",
        "insert_paragraph_after_anchor",
        "validate",
    },
    factory=DocxAdapter,
)
```

The important part is not the exact API shape. The important part is:

- adapters are registered explicitly
- selection is owned by code
- the model consumes capabilities after selection

## Operational Rule

The LLM-facing workflow should only ever see:

- file path or URI
- selected adapter name
- capability set
- validation result

It should not see raw library-routing concerns such as "choose python-docx or unzip OOXML manually." Those are implementation details below the agent workflow layer.

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
