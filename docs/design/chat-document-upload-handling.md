# Design: Chat Document Upload Handling

**Status:** draft
**Author:** Codex architect
**Date:** 2026-03-28

## Problem Statement

Users need to send `doc`, `docx`, and `pdf` files to agents from Slack and Discord. Those files may contain text, headers, tables, and images. The system must help `codex` and `claude-code` read them reliably, and when a user wants changes, the output should become a Markdown artifact committed to GitHub rather than a rewritten binary file returned over chat.

## Recommendation

Recommendation:

1. master should ingest and stage the original uploaded file only
2. agent should own document conversion to Markdown
3. agent should operate on the derived Markdown artifact, not on the original binary document
4. for modification requests, the agent should commit the Markdown result and return the GitHub URL

This keeps the architecture coherent:

- master handles chat-platform file retrieval and container staging
- agent handles document semantics and conversion
- both `codex` and `claude-code` consume the same staged file path and the same project-owned conversion CLI

## Why Conversion Belongs in the Agent

Master-side conversion looks simpler at first, but it creates the wrong coupling.

### Problems with master-side conversion

- master would need document-conversion dependencies for every routed request
- conversion output is only useful inside the agent workspace anyway
- modification requests require the agent to commit the Markdown artifact, so the conversion result belongs in the agent repo context
- any conversion fallback or cleanup logic would need to be duplicated across agent adapters

### Benefits of agent-side conversion

- conversion happens where the work product lives
- the same Markdown artifact can be committed directly by the agent
- both `codex` and `claude-code` can be instructed to use the same project CLI
- the master remains a transport and staging layer, not a document-processing service

## Platform Ingestion Constraints

The current codebase already has partial attachment handling:

- [slack_app.py](/workspace/repo/src/master/slack_app.py) extracts image file URLs from Slack events
- [discord_app.py](/workspace/repo/src/master/discord_app.py) reads text attachments inline and extracts image URLs
- [router.py](/workspace/repo/src/master/router.py) stages Slack private images into the agent container

This feature should extend that model from image-only and text-only attachments to staged document attachments.

### Slack

Slack file objects expose private download URLs and require an OAuth token with `files:read` for download.

### Discord

Discord message attachments expose filename, content type, and URL directly on the message object.

## Supported File Types

### v1

- `.doc`
- `.docx`
- `.pdf`

### Notes

- `.doc` is the riskiest format because it is legacy binary Word, not OOXML
- `.docx` is the strongest case for structured extraction
- `.pdf` is readable but semantically weaker, especially for tables and layout-heavy content

## End-to-End Flow

Example request:

`Please read the uploaded docx and summarize the tables`

### 1. User uploads the file in Slack or Discord

The uploaded file is received by the master frontend.

### 2. Master identifies supported document attachments

Master should normalize attachments into a document-attachment payload such as:

```json
{
  "filename": "example.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "source_url": "...",
  "platform": "slack"
}
```

### 3. Master downloads and stages the original file

Master should:

- download the original file bytes
- copy the file into the target agent container
- place it under a request-scoped staging directory

Suggested path shape:

- `/workspace/repo/.attachments/<request-id>/source/example.docx`

### 4. Master augments the prompt

Master should tell the agent:

- where the staged original file lives
- that it is a document attachment
- that document conversion should be performed through the project CLI

The prompt should mention the staged path, not the original Slack/Discord URL.

### 5. Agent converts the file to Markdown

The agent should invoke a project-owned CLI, for example:

- `agent-doc ingest /workspace/repo/.attachments/<request-id>/source/example.docx`

The output should be a derived document bundle, for example:

- `/workspace/repo/.attachments/<request-id>/derived/document.md`
- `/workspace/repo/.attachments/<request-id>/derived/assets/...`
- `/workspace/repo/.attachments/<request-id>/derived/manifest.json`

### 6. Agent reads and works from the derived Markdown

The agent should use the Markdown file plus manifest and extracted assets to:

- read text and headers
- inspect tables
- inspect extracted images
- make requested edits against Markdown

### 7. If the user wants modifications

The agent should:

- update `document.md`
- keep referenced extracted assets as needed
- commit the Markdown artifact to GitHub
- return the repository URL for the committed document

The system should not attempt to send a rewritten `docx` or `pdf` back to the user in v1.

## Proposed Master Changes

### `src/master/slack_app.py`

Add document-attachment extraction alongside image extraction.

Expected behavior:

- accept supported Slack file types for `doc`, `docx`, and `pdf`
- pass normalized document attachment metadata into the router

### `src/master/discord_app.py`

Extend current attachment handling:

- keep inline reading for plain text attachments
- treat `doc`, `docx`, and `pdf` as staged document attachments instead of inline text

### `src/master/router.py`

Add staging support for document attachments, parallel to image staging.

Suggested additions:

- `DocumentAttachment` normalized dataclass
- `_stage_documents(...)`
- platform-specific download helpers for Slack and Discord
- prompt augmentation that lists staged document paths

### Routing contract

The dispatcher should receive:

- original text prompt
- image URLs if any
- staged document paths if any

## Proposed Agent Changes

### Project-owned CLI

Add a project CLI such as:

- `agent-doc ingest <path>`
- `agent-doc inspect <derived-md-or-manifest>`
- `agent-doc extract-images <path>`
- `agent-doc apply-edit <path> --spec <json>`

For this feature, `ingest` is the key operation.

### `ingest` responsibilities

- detect file format
- normalize when needed
- convert to Markdown
- extract images
- preserve tables and headings as well as possible
- emit a manifest that records:
  - source file
  - detected format
  - derived markdown path
  - extracted assets
  - warnings and unsupported constructs

## Format-Specific Conversion Strategy

### `.doc`

Recommendation: normalize to `.docx` first using a headless office converter.

Reason:

- `.doc` is legacy binary
- downstream Markdown extraction is much easier once the file is normalized to OOXML

### `.docx`

Recommendation: convert to Markdown with a `.docx`-aware converter that preserves structure and extracted media.

Desired output:

- headings become Markdown headings
- paragraph flow preserved
- tables become Markdown or HTML tables
- images are extracted to asset files and referenced from Markdown

### `.pdf`

Recommendation: use a PDF-first extractor that can emit Markdown and extract images.

Desired output:

- page text becomes Markdown text
- headings are inferred where possible
- tables are preserved best-effort
- images are extracted to asset files and referenced or described in manifest

## Why Markdown Is the Right Intermediate Artifact

Markdown is the right agent-facing format because:

- both `codex` and `claude-code` handle it naturally
- it is diffable and reviewable in Git
- it works well with extracted asset folders
- it matches the required end state when the agent commits the result

The original binary upload should be treated as an input artifact, not the main editing surface.

## Detailed Decision: Master vs Agent Responsibilities

### Master owns

- chat-platform attachment discovery
- platform-authenticated download
- staging original binaries into the container
- passing staged paths into the routed prompt

### Agent owns

- file-type detection for conversion
- conversion to Markdown
- extraction of document assets
- edits to Markdown artifacts
- commit/push of the resulting Markdown document

This split is the most stable architecture for both `codex` and `claude-code`.

## Output Contract for Modification Requests

When the user asks for modifications:

1. agent edits the derived Markdown
2. agent commits the Markdown and extracted assets
3. agent pushes the branch
4. agent returns:
   - commit SHA or PR URL
   - repository path to the Markdown document
   - any warnings about unsupported content

It should not return a binary attachment as the primary result.

## Key Risks

### `.doc` support risk

Legacy Word conversion quality may vary. This is the highest-risk format in the requirement.

### `.pdf` semantic quality risk

PDF extraction is inherently weaker than `docx`, especially for tables, headers, and complex layouts.

### Markdown fidelity risk

Some document structure will not round-trip cleanly into Markdown. The system must surface warnings instead of pretending full fidelity.

## Recommended v1 Boundaries

- accept `doc`, `docx`, `pdf` uploads from Slack and Discord
- stage originals in the agent workspace
- convert in the agent
- edit Markdown only
- commit Markdown artifacts to GitHub for modification requests

Do not promise in v1:

- binary document round-trip fidelity
- perfect PDF table extraction
- original-file regeneration back to `docx` or `pdf`

## References

- Slack file object: https://docs.slack.dev/reference/objects/file-object/
- Discord message attachments: https://discord.com/developers/docs/resources/message#attachment-object
- Pandoc manual: https://pandoc.org/MANUAL.html
- Mammoth.js README: https://github.com/mwilliamson/mammoth.js
- PyMuPDF docs: https://pymupdf.readthedocs.io/
- PyMuPDF4LLM markdown extraction: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html
