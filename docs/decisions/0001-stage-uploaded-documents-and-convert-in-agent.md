---
title: "ADR-0001: Stage uploaded documents in master and convert them in the agent"
status: accepted
date: 2026-03-28
decision-makers: [project maintainers]
consulted: [architecture review completed]
informed: [master and agent operators]
---

## Context and Problem Statement

Users need to send office-style documents to agents through Slack and Discord. For v1, the active scope is `docx` and `pdf`. Those documents may contain headings, tables, images, and layout structure that are difficult for `codex` and `claude-code` to consume directly from binary files. The system needs a stable ingestion model that works across both agent adapters and supports two user intents:

- read and analyze the document
- modify the document and persist the result in GitHub

The key architecture question is where document conversion should happen and what artifact the agent should edit.

## Decision Drivers

- Works in both `codex` and `claude-code`
- Keeps the master runtime focused on chat-platform integration, not document semantics
- Produces an agent-friendly working artifact
- Supports eventual commit/push of the modified output to GitHub
- Handles `docx` and `pdf` with a single workflow shape in v1
- Minimizes duplicated conversion logic across adapters

## Considered Options

1. Master stages the original upload; agent converts to Markdown and works on the Markdown artifact
2. Master converts the upload to Markdown before routing to the agent
3. Agent works directly on uploaded binary documents without Markdown conversion

## Decision Outcome

*Chosen option:* Option 1 — master stages the original upload; agent converts to Markdown and works on the Markdown artifact — because it cleanly separates platform transport from document semantics, gives both agent adapters the same working format, and aligns the editable artifact with the Git commit target.

### Consequences

- *Good:* Master stays focused on Slack/Discord attachment retrieval and staging.
- *Good:* The agent owns the artifact it will eventually commit.
- *Good:* Both `codex` and `claude-code` can use the same project-owned document-conversion interface.
- *Good:* The system gets a consistent intermediate format for reading, tables, and extracted images.
- *Bad:* The agent image must carry document-conversion dependencies.
- *Bad:* Legacy `.doc` remains a higher-risk input than `docx`.
- *Bad:* Markdown conversion will not preserve all binary document semantics perfectly.

### Confirmation

We will consider this decision validated when:

- Slack and Discord uploads of supported document types are staged into the agent workspace
- the agent can convert staged `docx` and `pdf` files into a Markdown artifact plus extracted assets
- both `codex` and `claude-code` can read the converted artifact through the same project workflow
- modification requests end with a committed Markdown result and a returned GitHub URL

## Pros and Cons of the Options

### Option 1: Master stages originals; agent converts to Markdown

Master downloads and stages the original attachment into the agent workspace. The agent then runs a project-owned document conversion flow and edits the derived Markdown artifact.

- Pro: Keeps platform-specific download logic in master and document-specific logic in the agent.
- Pro: The converted Markdown lives in the same repo/workspace context where the agent works and commits.
- Pro: Provides the same working model to both `codex` and `claude-code`.
- Pro: Avoids duplicating conversion logic in master and agent runtimes.
- Con: The agent image needs more dependencies.
- Con: Conversion warnings and fidelity limits must be surfaced clearly.

### Option 2: Master converts uploads before routing

Master downloads the uploaded file, converts it to Markdown, and then passes the converted artifact to the agent.

- Pro: The agent receives a ready-to-read format immediately.
- Pro: Master could theoretically standardize conversion once for all adapters.
- Con: Master becomes responsible for document semantics and conversion dependencies.
- Con: The converted artifact still needs to be moved into the agent workspace for editing and commit.
- Con: This couples routing infrastructure to evolving document-conversion logic.

### Option 3: Agent works directly on binary documents

The uploaded file is staged and the agent reads or edits the original binary document directly.

- Pro: Avoids introducing a Markdown intermediate artifact.
- Pro: Keeps the original file as the primary source.
- Con: `codex` and `claude-code` do not consume binary office formats as naturally as Markdown.
- Con: Tables, images, and extracted structure become harder to present consistently.
- Con: Returning a GitHub URL to a modified result is less natural if the working artifact is still binary.

## Implementation Notes

This ADR records the intended v1 shape:

- master stages the original uploaded file only
- agent converts the staged file to Markdown and extracted assets
- agent reads and edits Markdown, not the original binary file
- modification requests return a GitHub URL to the committed Markdown artifact instead of returning a rewritten binary file over chat

## Resolved Follow-Up Decisions

### 1. Conversion location

Decision: document conversion happens in the agent, not in master.

Rationale:

- master should remain a transport and staging layer
- the derived Markdown artifact belongs in the agent working context
- both `codex` and `claude-code` can follow the same workflow once the staged file is present

### 2. How the agent is told what to do

Decision: do not inject document-handling instructions into the routed prompt.

Instead:

- repo-level `AGENTS.md` and `.claude/CLAUDE.md` carry the workflow rule
- master provides request-specific state through a manifest file

Rationale:

- the prompt should stay close to the user’s actual message
- attachment-handling policy should live in versioned repo instructions, not per-message prompt prose

### 3. Request-state transport

Decision: use a per-request manifest file plus one exec-time environment variable:

- `AGENT_REQUEST_MANIFEST=<absolute path>`

No additional attachment-specific env vars are required in v1.

Rationale:

- the manifest contains all request attachment state in one place
- the env var gives the agent a stable discovery point
- this avoids prompt augmentation and avoids proliferating many small env vars

### 4. Request scoping

Decision: create a unique request-specific attachment directory for every routed message.

Rationale:

- prevents cross-message state leakage
- works for concurrent requests
- keeps cleanup simple
- avoids a shared mutable `current-request` location

### 5. File-format scope for v1

Decision: drop legacy `.doc` from v1. Focus the first implementation on `docx` and `pdf`.

Rationale:

- `.doc` conflicts with the goal of keeping the toolchain simple and headless-container friendly
- supporting `.doc` well would likely force a heavier normalization dependency
- `docx` and `pdf` cover the cleaner first implementation path

### 6. Conversion toolchain for v1

Decision: use `Mammoth` for `docx` and `PyMuPDF4LLM` for `pdf`.

Rationale:

- this keeps the toolchain headless-container friendly
- it matches the requirement focus on headers, text, tables, and basic image extraction
- it avoids dragging in a heavier office-suite dependency for the first implementation
- each tool is a good fit for its native format instead of forcing one generic converter across both

### 7. Original uploaded binary retention

Decision: whether the original uploaded binary is committed should be left to the project/agent workflow, not enforced as a global platform rule.

Rationale:

- some projects may want the original upload preserved for traceability
- other projects may want to avoid binary files in the repo entirely
- this is better decided at the agent/project work level than hard-coded in the transport layer

### 8. Request-specific storage location

Decision: request-specific attachment storage should live outside `/workspace/repo/`, for example under `/workspace/message/<request-id>/...`.

Rationale:

- keeps transient request inputs separate from Git-tracked repo content
- reduces the chance of accidentally committing staged source files
- gives the platform a cleaner lifecycle boundary for request-scoped cleanup
- the agent can still read absolute paths under `/workspace/`, so this does not block processing

## Remaining Discussion Items

- none at the ADR level

### Discussion Note: Request-Specific Storage Location

This has not been decided yet, but the current direction under discussion is:

- request-scoped input artifacts may live outside the Git worktree, for example `/workspace/message/<request-id>/...`
- commit-worthy derived Markdown artifacts may later be written into `/workspace/repo/...`

This is technically viable because the agent can read absolute paths under `/workspace/`, not only files under `/workspace/repo/`.

The decision is still open because there is a tradeoff between:

- cleaner separation of transient input artifacts from Git-tracked output
- versus simpler path handling when everything stays under the repo worktree

### Discussion Note: Conversion Toolchain Comparison

Chosen option for v1: `Mammoth + PyMuPDF4LLM`

| Option | `docx` tool | `pdf` tool | Type | Language | Operational overhead | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Specialized split stack | Mammoth | PyMuPDF4LLM / PyMuPDF | library + library | JavaScript for Mammoth; Python for PyMuPDF | medium | Best fit for simple headless extraction; two toolchains instead of one |
| Unified converter | MarkItDown | MarkItDown | library and CLI-style entrypoint | Python | low to medium | Single interface is attractive, but less format-specific control |
| General-purpose + PDF-specific | Pandoc | PyMuPDF4LLM / PyMuPDF | CLI + library | Pandoc executable; Python for PyMuPDF | medium | Mature, but more generic than necessary for `docx` |
| Custom extraction stack | custom `docx` parser flow | custom PDF extraction flow | library code | Python | high | Highest control, highest maintenance burden |

Additional notes:

- Mammoth is purpose-built for `docx` conversion and is structurally a better fit than a generic converter when the goal is headings, text, tables, and basic images.
- PyMuPDF4LLM / PyMuPDF is a strong PDF-side fit for headless extraction and LLM-oriented Markdown output.
- MarkItDown is attractive if a single conversion surface is more important than format-specific tuning.
- Pandoc is mature, but for this use case it looks more like a fallback or secondary tool than the most natural primary path.

## References

- Slack file object: https://docs.slack.dev/reference/objects/file-object/
- Discord attachment object: https://discord.com/developers/docs/resources/message#attachment-object
