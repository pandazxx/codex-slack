# Cloud Workspace Office File Analysis

**Status:** draft
**Author:** Codex architect
**Date:** 2026-03-28
**Related ADRs:** ADR-0001

## Problem Statement

Adding cloud-backed agent workspaces is not only a storage and authentication problem. The agent also needs a practical way to read, interpret, and update office documents stored in those workspaces, including text, tables, and embedded images where feasible.

## Goals

- Determine how an agent should process `docx`, `xlsx`, `pptx`, and `pdf` files in a headless container
- Separate what should be handled locally from what can be delegated to provider-native APIs
- Identify realistic support levels for text, tables, and images
- Keep the first implementation reliable enough for automation

## Non-Goals

- Building a full-fidelity Office rendering engine
- Matching desktop Office behavior for every document feature
- Solving OCR, layout-preserving round-trips, or SmartArt/chart fidelity in v1

## Recommendation

Recommendation for v1:

1. Treat office files as local workspace artifacts first.
2. Sync them down from cloud storage into the agent workspace.
3. Use format-specific local parsers and writers inside the agent image.
4. Use provider-native document APIs only where they add clear value and have strong headless support.
5. Do not make provider-native office APIs the primary document-processing path for v1.

The core architectural reason is straightforward: cloud-storage integrations are optional, but office-file handling is fundamental to the agent’s job. The agent needs a provider-independent document toolchain even when cloud APIs are missing, partial, or vendor-specific.

## Agent-Side File Handling Strategy

### `docx`

#### Practical support level

- Read text: strong
- Read tables: strong
- Read embedded inline images: moderate
- Update text and tables: strong
- Update embedded images: moderate
- Complex layout fidelity: limited

#### Why

`.docx` is an Office Open XML package, which is relatively automation-friendly. The `python-docx` documentation shows support for opening existing documents, adding and editing paragraphs, building tables, and inserting pictures. The same docs also note that picture support is currently limited to inline pictures, which is an important constraint for documents that use floating layout heavily.

#### Design implication

For `docx`, the agent can usually:

- extract paragraphs and table content
- insert or replace generated text
- add or replace simple tables
- inspect and extract at least some embedded images

But the agent should not promise lossless handling of:

- tracked changes
- comments
- floating shapes
- advanced fields
- complex templates

### `xlsx`

#### Practical support level

- Read cells, formulas, and sheets: strong
- Read tables: strong
- Read embedded images: moderate
- Update cells and tables: strong
- Preserve advanced Excel features: limited to moderate

#### Why

The `openpyxl` documentation covers worksheet tables and image handling, which makes it a strong local tool for structured spreadsheet edits. This is a good fit for agent tasks that mostly operate on cells, formulas, ranges, named tables, and workbook metadata.

#### Design implication

For `xlsx`, the agent can usually:

- read and write structured tabular data safely
- append rows and update formula-driven sheets
- add or inspect workbook tables
- place or inspect embedded images in some cases

But the agent should be conservative around:

- pivot tables
- macros
- complex charts
- external data connections
- workbook features that depend on desktop Excel behavior

### `pptx`

#### Practical support level

- Read slide text: strong
- Read tables: moderate to strong
- Read images: strong
- Update text boxes, tables, and pictures: moderate to strong
- Preserve complex presentation semantics: limited

#### Why

The `python-pptx` documentation exposes pictures as shape/image objects and documents table-capable placeholders and picture operations. That makes slides automatable at the level of text blocks, tables, and inserted images.

#### Design implication

For `pptx`, the agent can usually:

- read slide text and speaker-facing structure
- iterate shapes and inspect pictures
- create or update simple tables
- add and replace images

But the agent should not promise complete fidelity for:

- SmartArt
- advanced charts
- animations
- transitions
- embedded media
- heavily themed or design-sensitive decks

### `pdf`

#### Practical support level

- Read machine-readable text: moderate
- Read images: moderate
- Read tables: weak to moderate
- Update existing content in-place: weak
- Generate or assemble new PDFs: moderate

#### Why

The `pypdf` documentation explicitly states that it can retrieve text and images, but it is not an OCR engine. It also explains that PDF lacks a semantic layer for concepts such as paragraphs, headers, and tables. That means PDF text extraction can work well for digitally generated PDFs, but table understanding and semantic editing are inherently unreliable.

#### Design implication

For `pdf`, the agent should generally:

- extract text when the PDF already contains text
- extract images where possible
- treat table extraction as best-effort
- avoid promising precise semantic edits to existing PDFs

If a workflow requires high-confidence PDF editing, the safer approach is usually:

- convert from a structured source document
- generate a replacement PDF
- or add annotations rather than rewrite layout-heavy content in place

## Can the Agent Read Images and Tables?

### Tables

- `docx`: yes, with good support
- `xlsx`: yes, with strong support
- `pptx`: yes, for explicit table shapes
- `pdf`: only best-effort, because tables are visual constructs rather than guaranteed semantic objects

### Images

- `docx`: yes for at least inline pictures; broader shape support is more limited
- `xlsx`: yes in many cases through workbook drawing/image handling
- `pptx`: yes, this is one of the stronger cases
- `pdf`: yes for embedded image objects, but not for understanding image meaning without vision or OCR layers

## Cloud-Platform Native APIs

### Google Workspace APIs

Google is the strongest provider if the product is willing to work with Google-native document types instead of only raw office files.

#### What the APIs support

- Google Docs API: structured reads and writes, including tables
- Google Docs API: inline images with documented constraints
- Google Sheets API: structured read/write for cells and ranges
- Google Slides API: create and edit tables and images
- Drive API: export Google-native files to formats such as PDF or Office-compatible formats

#### Architectural implication

If the file is a native Google Doc, Sheet, or Slide, the agent can manipulate document structure through provider APIs instead of editing exported files locally. That is powerful, but it changes the programming model from "work on files" to "work on provider-native objects."

#### Constraint

This path is strongest only for Google-native document types. For uploaded `docx`, `xlsx`, `pptx`, and `pdf` files kept as raw binaries in Drive, the agent should still treat them as files and use local tooling.

### Microsoft 365 / Graph APIs

Microsoft has strong API support for Excel, but the picture is not symmetric across Word and PowerPoint.

#### What the APIs support

- Microsoft Graph Excel API supports CRUD-style operations on workbook objects like tables, ranges, and charts
- Microsoft Graph supports file upload/download and some format conversion through drive items
- Office JavaScript APIs provide rich Word and PowerPoint document models, but they are designed for Office add-ins rather than headless backend agents

#### Architectural implication

If the project needs deep workbook automation and the files live in OneDrive or SharePoint, Microsoft Graph can be a first-class processing API for `xlsx`. For Word and PowerPoint, the reviewed Microsoft Graph docs do not show an equivalent general-purpose document-content REST API, so the agent should continue to treat `docx` and `pptx` as local files.

#### Constraint

Office.js is not a good primary path for this project because it assumes an Office host application rather than a headless container runtime.

### Nextcloud, Synology Drive, and Dropbox

These platforms are primarily storage and sync layers in this context.

#### What the APIs support

- file access
- upload and download
- sync workflows
- browser-based office editing for users in some deployments

#### Architectural implication

The agent should not depend on provider-native document object models here. For these backends, office-file intelligence should come from the local agent toolchain after sync.

## Proposed Capability Model

Support should be declared by operation, not by vague "Office support" language.

### v1 support target

- `docx`: read/write text and tables, extract inline images where available
- `xlsx`: read/write sheets, cells, and tables; limited image support
- `pptx`: read/write slide text, simple tables, and pictures
- `pdf`: extract text and images where available; best-effort table extraction; no promise of full semantic rewriting

### v1 non-goals

- full-fidelity round-trip preservation for complex layout documents
- tracked-changes-aware Word editing
- macro-safe Excel editing
- SmartArt and animation editing in PowerPoint
- OCR-dependent understanding of scanned PDFs

## Implementation Notes

The agent image should likely include:

- a `docx` parser/writer
- an `xlsx` parser/writer
- a `pptx` parser/writer
- a PDF reader and image extractor

An additional conversion/rendering toolchain may still be useful later for:

- format normalization
- preview generation
- PDF regeneration from structured sources

But that should be treated as a second-phase enhancement, not the base requirement for v1.

## Recommendation Summary

The project should separate storage choice from document-processing choice:

- Cloud providers decide how files are stored, synchronized, and authenticated.
- The agent’s local office toolchain decides how files are parsed and updated.
- Provider-native office APIs are optional accelerators, not the foundation.

That separation keeps the design robust:

- Nextcloud, Synology, and Dropbox remain viable because the agent does not depend on their office APIs.
- Google native docs can be supported later with dedicated API adapters.
- Microsoft Excel can get special handling through Graph without forcing Word or PowerPoint into the same model.

## References

- python-docx quickstart: https://python-docx.readthedocs.io/en/latest/user/quickstart.html
- python-docx shapes: https://python-docx.readthedocs.io/en/latest/user/shapes.html
- python-docx table merge analysis: https://python-docx.readthedocs.io/en/latest/dev/analysis/features/table/cell-merge.html
- openpyxl images: https://openpyxl.readthedocs.io/en/stable/images.html
- openpyxl worksheet tables: https://openpyxl.readthedocs.io/en/3.1.0/worksheet_tables.html
- python-pptx image API: https://python-pptx.readthedocs.io/en/stable/api/image.html
- python-pptx placeholders: https://python-pptx.readthedocs.io/en/latest/user/placeholders-understanding.html
- pypdf overview: https://pypdf.readthedocs.io/en/6.1.0/
- pypdf text extraction: https://pypdf.readthedocs.io/en/3.16.1/user/extract-text.html
- pypdf image extraction: https://pypdf.readthedocs.io/en/latest/user/extract-images.html
- Google Docs tables: https://developers.google.com/docs/api/how-tos/tables
- Google Docs structural edit rules: https://developers.google.com/docs/api/concepts/rules-behavior
- Google Slides tables: https://developers.google.com/workspace/slides/api/samples/tables
- Google Slides images: https://developers.google.com/slides/api/guides/add-image
- Google Drive export: https://developers.google.com/drive/api/reference/rest/v3/files/export
- Microsoft Graph Excel overview: https://learn.microsoft.com/en-us/graph/excel-concept-overview
- Microsoft Graph write to workbook: https://learn.microsoft.com/en-us/graph/excel-write-to-workbook
- Office JavaScript API object model: https://learn.microsoft.com/en-us/office/dev/add-ins/develop/office-javascript-api-object-model
