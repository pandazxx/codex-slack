---
title: "ADR-0001: Stage and convert uploaded documents in master"
status: accepted
date: 2026-03-28
decision-makers: [project maintainers]
consulted: [architecture review completed]
informed: [master and agent operators]
---

## Context and Problem Statement

Users need to send `docx` and `pdf` files to agents through Slack and Discord. Those files may contain headings, tables, and embedded images that `codex` and `claude-code` do not consume reliably in raw binary form. The system needs a stable ingestion model that:

- works in both agent adapters
- keeps request attachment handling out of the prompt body
- preserves extracted images alongside the converted Markdown
- gives the agent a deterministic artifact to read and optionally commit

The key architecture question is where document conversion should happen and how the converted Markdown and extracted images should be exposed to the agent.

## Decision Drivers

- Works in both `codex` and `claude-code`
- Keeps the prompt close to the user’s original message
- Gives the agent a stable, deterministic input contract
- Preserves extracted images with paths that the generated Markdown can reference correctly
- Supports modification workflows that end in a Git commit and returned URL
- Keeps request-scoped artifacts outside `/workspace/repo/`

## Considered Options

1. Master stages the original upload; agent converts to Markdown and works on the Markdown artifact
2. Master stages the original upload, converts it to Markdown, and exposes both source and derived artifacts through the request manifest
3. Agent works directly on uploaded binary documents without Markdown conversion

## Decision Outcome

*Chosen option:* Option 2 — master stages the original upload, converts it to Markdown, and exposes both source and derived artifacts through the request manifest.

This is the best fit because it keeps document conversion deterministic and centralized, lets master place extracted images and generated Markdown together in request storage with correct relative references, and lets both `codex` and `claude-code` consume the same derived artifact without needing to own the conversion toolchain.

### Consequences

- *Good:* The agent receives a ready-to-read Markdown artifact and image assets through a stable manifest contract.
- *Good:* Master can guarantee that generated Markdown references extracted images correctly before dispatch.
- *Good:* The agent-side runtime stays simpler because it no longer owns document conversion.
- *Good:* Both adapters use the same request-storage and manifest workflow.
- *Bad:* Master now owns document-conversion dependencies and failure modes.
- *Bad:* Conversion throughput and resource usage move into the master runtime.
- *Bad:* Markdown conversion still will not preserve all binary document semantics perfectly.

### Confirmation

We will consider this decision validated when:

- Slack and Discord uploads of supported document types are staged into request-scoped storage
- master converts staged `docx` and `pdf` files into Markdown plus extracted assets
- the generated Markdown refers to extracted images with valid relative paths
- both `codex` and `claude-code` can read the converted artifact through the same manifest-driven workflow
- modification requests can end with committed Markdown output and a returned GitHub URL

## Pros and Cons of the Options

### Option 1: Master stages originals; agent converts to Markdown

Master downloads and stages the original attachment into request storage. The agent then runs a project-owned document conversion flow and edits the derived Markdown artifact.

- Pro: Keeps platform-specific download logic in master and document-specific logic in the agent.
- Pro: The converted Markdown lives near the agent’s work context.
- Con: The agent must carry the conversion toolchain.
- Con: Extracted image handling and Markdown path correctness become agent concerns.
- Con: Both adapters still need to be told to run conversion before they can read the file.

### Option 2: Master stages originals and converts before routing

Master downloads the uploaded file, converts it to Markdown, extracts images, stores those derived artifacts in request storage, and passes only the manifest location to the agent.

- Pro: The agent receives a ready-to-read artifact immediately.
- Pro: Master can normalize output layout and image paths once for both adapters.
- Pro: The request manifest cleanly carries both source and derived artifact metadata.
- Con: Master becomes responsible for document semantics and conversion dependencies.
- Con: Master must handle conversion failures before dispatch.

### Option 3: Agent works directly on binary documents

The uploaded file is staged and the agent reads or edits the original binary document directly.

- Pro: Avoids introducing a Markdown intermediate artifact.
- Pro: Keeps the original file as the primary source.
- Con: `codex` and `claude-code` do not consume binary office formats as naturally as Markdown.
- Con: Tables, images, and extracted structure become harder to present consistently.
- Con: Returning a GitHub URL to a modified result is less natural if the working artifact is still binary.

## Implementation Notes

This ADR records the intended v1 shape:

- master stages the uploaded file into request-scoped storage
- master converts `docx` and `pdf` into Markdown before dispatch
- master extracts images into request-scoped derived storage and writes Markdown that refers to those images with relative paths
- master writes a per-request manifest and injects `AGENT_REQUEST_MANIFEST`
- the agent reads derived Markdown and assets from request storage
- when modifications are requested, the agent writes durable output into `/workspace/repo/...`, commits it, and returns a GitHub URL instead of returning a rewritten binary file

## Resolved Follow-Up Decisions

### 1. Conversion location

Decision: document conversion happens in master, not in the agent.

Rationale:

- master can produce one deterministic derived artifact for both adapters
- master can guarantee image extraction and Markdown image references as part of the same conversion step
- the agent no longer needs to own conversion dependencies or conversion workflow logic

### 2. How the agent is told what to do

Decision: do not inject document-handling instructions into the routed prompt.

Instead:

- repo-level `AGENTS.md` and `.claude/CLAUDE.md` carry the standing workflow rule
- master provides request-specific state through a manifest file

Rationale:

- the prompt should stay close to the user’s actual message
- attachment-handling policy should live in versioned repo instructions, not per-message prompt prose

### 3. Request-state transport

Decision: use a per-request manifest file plus one exec-time environment variable:

- `AGENT_REQUEST_MANIFEST=<absolute path>`

No additional attachment-specific env vars are required in v1.

### 4. Request scoping

Decision: create a unique request-specific attachment directory for every routed message.

### 5. File-format scope for v1

Decision: drop legacy `.doc` from v1. Focus the first implementation on `docx` and `pdf`.

### 6. Conversion toolchain for v1

Decision: use `Mammoth` for `docx` and `PyMuPDF4LLM` for `pdf`.

Rationale:

- this keeps the toolchain headless-container friendly
- it matches the requirement focus on headers, text, tables, and basic image extraction
- it avoids dragging in a heavier office-suite dependency for the first implementation

Current implementation note:

- the accepted target toolchain remains `Mammoth + PyMuPDF4LLM`
- the repository currently ships a working baseline converter in `src/master/document_convert.py`
- that baseline uses an internal DOCX XML fallback and optional `pypdf` extraction for PDFs until the final toolchain is wired in

### 7. Original uploaded binary retention

Decision: whether the original uploaded binary is committed is left to the project/agent workflow, not enforced as a global platform rule.

### 8. Request-specific storage location

Decision: request-specific attachment storage lives outside `/workspace/repo/`, under `/workspace/message/<request-id>/...`, via a master-managed request-storage named volume shared between master and the agent container.

### 9. Request-storage lifecycle

Decision: master manages request storage and cleans up the request directory after a successful reply. Failed requests are retained for debugging.

### 10. Request-storage ownership and permissions

Decision: master owns request storage writes through its own writable mount of the per-agent request-storage named volume. The agent mounts the same request storage read-only.

Rationale:

- request manifests, uploaded source files, converted Markdown, and extracted images are transport-scoped artifacts produced by master
- agent-side durable output belongs in the repo, not in request storage
- read-only request storage reduces accidental mutation of staged source and derived artifacts
- using a named volume instead of a host bind avoids host-path coupling and remote-Podman bind-source provisioning problems

### 13. Request-storage volume shape

Decision: use one named volume per agent, such as `agent-messages-<agent-name>`.

Mount shape:

- master mount path: `/workspace/messages/<agent-name>` read-write
- agent mount path: `/workspace/message` read-only

Rationale:

- preserves direct master-side filesystem writes without helper-container indirection
- keeps agent-side paths stable and simple
- aligns request storage with the repository's broader named-volume-first runtime model

### 11. Attachment acceptance policy

Decision:

- best-effort acceptance if either MIME type or filename extension looks correct
- one global size cap for supported attachments
- hard rejection for unsupported or oversized document attachments

### 12. Durable output placement

Decision: final durable output placement inside `/workspace/repo/...` is owned by the project/agent workflow, not by a platform-fixed path convention.

## Discussion Note: Conversion Toolchain Comparison

Chosen option for v1: `Mammoth + PyMuPDF4LLM`

| Option | `docx` tool | `pdf` tool | Type | Language | Operational overhead | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Specialized split stack | Mammoth | PyMuPDF4LLM / PyMuPDF | library + library | JavaScript for Mammoth; Python for PyMuPDF | medium | Best fit for simple headless extraction; two toolchains instead of one |
| Unified converter | MarkItDown | MarkItDown | library and CLI-style entrypoint | Python | low to medium | Single interface is attractive, but less format-specific control |
| General-purpose + PDF-specific | Pandoc | PyMuPDF4LLM / PyMuPDF | CLI + library | Pandoc executable; Python for PyMuPDF | medium | Mature, but more generic than necessary for `docx` |
| Custom extraction stack | custom `docx` parser flow | custom PDF extraction flow | library code | Python | high | Highest control, highest maintenance burden |

Additional notes:

- Mammoth is purpose-built for `docx` conversion and is structurally a better fit than a generic converter when the goal is headings, text, tables, and basic images.
- `docx2python` remains a future alternative if later work prioritizes Python-native extraction depth or optional comment extraction over direct Markdown simplicity.
- PyMuPDF4LLM / PyMuPDF is a strong PDF-side fit for headless extraction and LLM-oriented Markdown output.

## References

- Slack file object: https://docs.slack.dev/reference/objects/file-object/
- Discord attachment object: https://discord.com/developers/docs/resources/message#attachment-object
