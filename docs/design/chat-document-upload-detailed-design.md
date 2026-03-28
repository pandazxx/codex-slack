# Design: Chat Document Upload Detailed Discussion

**Status:** draft
**Author:** Codex architect
**Date:** 2026-03-28
**Related ADRs:** ADR-0001

## Purpose

ADR-0001 establishes the architectural split:

- master stages uploaded documents
- agent converts them to Markdown
- agent edits Markdown and returns a GitHub URL for modification requests

This document narrows the implementation choices that ADR-0001 leaves open.

## Questions To Nail Down

The ADR leaves three practical questions:

1. which conversion toolchain should be used for `doc`, `docx`, and `pdf`
2. what exact contract should master use when handing staged files to the agent
3. what artifacts should be committed when the user asks for modifications

## Recommendation Summary

Recommended v1 choices:

1. `.doc`: normalize with LibreOffice headless, then treat as `docx`
2. `.docx`: convert with Mammoth to Markdown and extracted images
3. `.pdf`: convert with PyMuPDF4LLM to Markdown and extracted images
4. master passes staged local file paths and attachment metadata only
5. agent commits derived Markdown, extracted assets, and a manifest; do not commit the original binary by default

## Toolchain Discussion

### `.doc`

#### Problem

Legacy `.doc` is not a modern structured XML package, so it is the weakest input format in the requirement.

#### Recommended v1 path

Use LibreOffice headless as a normalization step:

- input: `.doc`
- output: normalized `.docx`
- downstream handling: same as native `docx`

#### Why

- it avoids building a separate legacy Word parsing pipeline
- it reduces the number of direct conversion branches the agent needs
- it lets the stronger `docx` pipeline handle most of the actual document extraction

#### Risk

- conversion fidelity is variable for old Word files
- LibreOffice is a heavier dependency than the pure-Python path for `docx` and `pdf`

### `.docx`

#### Recommended v1 path

Use Mammoth for structure-oriented extraction to Markdown.

#### Why

- Mammoth is specifically designed for `.docx`
- it can output Markdown directly
- it supports extracted media and structural conversion more naturally than a generic text dump

#### Expected strengths

- headings
- paragraph flow
- lists
- images
- simple tables

#### Expected limits

- complex layout fidelity
- Word-specific advanced features
- tracked changes and comments

### `.pdf`

#### Recommended v1 path

Use PyMuPDF4LLM for Markdown extraction, with image extraction enabled.

#### Why

- it is explicitly aimed at LLM-facing Markdown extraction
- it is a better fit for this use case than trying to force PDF through a generic document converter
- it can emit Markdown and extract images in the same pipeline

#### Expected strengths

- page text
- rough structure
- extracted images
- best-effort tables

#### Expected limits

- table fidelity
- scanned/OCR-only PDFs
- complex multi-column layouts

### Why Not Pandoc As The Primary Tool?

Pandoc is excellent as a general conversion tool, but it is not the best primary fit here:

- `.doc` still needs normalization first
- `docx` extraction for LLM editing is better served by a Word-focused converter
- PDF-to-Markdown is not Pandoc’s strongest path

Pandoc remains useful later as an optional secondary tool for normalization, export, or debugging, but not as the primary v1 ingestion path.

## Master-To-Agent Contract

The master should not pass platform URLs to the agent. It should pass staged local paths and normalized metadata.

## Recommended Attachment Payload

Master should normalize each uploaded document into a payload like:

```json
{
  "kind": "document",
  "filename": "example.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "platform": "slack",
  "staged_path": "/workspace/repo/.attachments/req-123/source/example.docx"
}
```

## Prompt Contract

Master should prepend a structured attachment block to the routed prompt, for example:

```text
Attached documents:
- /workspace/repo/.attachments/req-123/source/example.docx (docx)

Use the project document ingestion command before reading or editing the file.
```

This keeps the prompt simple and adapter-neutral.

## Recommended Agent CLI Contract

The first required command is:

```bash
agent-doc ingest <staged-path>
```

Expected result:

- exit code `0` on success
- machine-readable output describing:
  - detected format
  - derived markdown path
  - extracted asset directory
  - warnings

Suggested JSON output shape:

```json
{
  "ok": true,
  "format": "docx",
  "source_path": "/workspace/repo/.attachments/req-123/source/example.docx",
  "derived_markdown_path": "/workspace/repo/.attachments/req-123/derived/document.md",
  "assets_dir": "/workspace/repo/.attachments/req-123/derived/assets",
  "warnings": []
}
```

This is the key compatibility point for both `codex` and `claude-code`.

## Artifact Layout

Recommended request-scoped layout:

```text
.attachments/
  req-123/
    source/
      example.docx
    derived/
      document.md
      manifest.json
      assets/
        image-001.png
        image-002.png
```

### Why this layout

- keeps source and derived artifacts separate
- makes cleanup straightforward
- gives the agent a predictable path structure
- makes it easy to choose what to commit

## What Should Be Committed?

Recommended default:

- commit `derived/document.md`
- commit `derived/manifest.json`
- commit `derived/assets/` if images were extracted and referenced
- do not commit `source/example.docx|pdf` by default

### Why not commit the original binary by default

- the requirement says the user-facing result should be the Markdown document URL
- the original upload is an input artifact, not the primary work product
- binary files create noisier diffs and higher repo weight

### Possible later option

If traceability becomes important, add an explicit opt-in mode to preserve originals. That should not be the v1 default.

## Manifest Requirements

The manifest should capture:

- source filename
- detected format
- converter used
- derived markdown path
- extracted assets
- warnings
- unsupported constructs

This gives the agent and the human reviewer a stable explanation of what happened during conversion.

## Tables, Headers, and Images

### Headers

The converter should preserve headings as Markdown headings where possible.

### Tables

Recommended rule:

- emit Markdown tables when simple and rectangular
- emit inline HTML tables when the source structure is too complex for Markdown
- record a warning in the manifest when table fidelity is uncertain

### Images

Recommended rule:

- always extract images to `derived/assets/`
- reference them from Markdown using relative paths
- if placement fidelity is uncertain, record a warning rather than pretending exact layout preservation

## Adapter-Agnostic Workflow

Both `codex` and `claude-code` should follow the same workflow:

1. read the prompt
2. locate staged file path
3. run `agent-doc ingest`
4. read the derived Markdown and manifest
5. perform the requested task
6. if editing is requested, modify Markdown and commit
7. return the GitHub URL

This is the reason the CLI contract matters more than any agent-specific prompt wording.

## Suggested v1 Guardrails

- reject files above a configurable size limit
- reject unsupported MIME types and extensions early
- record converter warnings in both CLI JSON output and user-facing response
- do not claim exact binary round-trip support
- do not attempt to regenerate `docx` or `pdf` in v1

## Open Decisions After This Discussion

Only a small set of implementation decisions should remain after this document:

- exact size limits for uploads
- whether LibreOffice is installed in the base image or a feature-specific image
- whether `agent-doc` is implemented in Python only or wraps external binaries more directly

## References

- LibreOffice document conversion help: https://help.libreoffice.org/latest/he/text/shared/autopi/01130000.html
- Mammoth.js README: https://github.com/mwilliamson/mammoth.js
- PyMuPDF4LLM docs: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm
- Pandoc manual: https://pandoc.org/MANUAL.html
