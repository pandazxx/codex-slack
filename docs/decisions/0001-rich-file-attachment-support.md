---
title: "ADR-0001: Rich file attachment support (inbound and outbound)"
status: accepted
date: 2026-03-24
decision-makers: [user, architect agent]
consulted: []
informed: []
---

## Context and Problem Statement

The system today handles a narrow set of file types in each direction. Slack inbound accepts images only. Discord inbound accepts images and plain-text code files (`.txt`, `.md`, `.csv`, `.py`, etc.). Neither platform has a path for the agent to return a modified file as an attachment. Users need to send richer documents (Word, Excel, PDF) and receive back amended versions of those files.

## Decision Drivers

- Zero friction for the user — no pre-processing or conversion before uploading
- Works across both Slack and Discord platforms
- Extensible to future channels without changing core logic
- Context window must not overflow on large files
- Discord's platform attachment cap (25 MB free tier, 500 MB Nitro) must not be a hard blocker for typical business documents

## Considered Options

### Inbound (user → agent)

1. *Server-side conversion in the master runtime* — download the file, convert docx/xlsx/pdf to text using `python-docx`, `openpyxl`, `pdfplumber`; inject inline into the prompt for small files; stage via `podman cp` and inject a Read-tool pointer for large files.
2. *Pass raw bytes to the Claude API as document blocks* — Claude API natively supports PDF as a document block; docx/xlsx are not supported.
3. *Ask users to convert before uploading* — rejected immediately; violates the zero-friction requirement.

### Outbound (agent → user)

1. *JSON envelope in stdout* — extend the agent's response to include `output_files: ["/workspace/..."]`; master parses, retrieves via `podman cp`, uploads to platform.
2. *Sidecar manifest file* — agent writes `/workspace/.agent-output/files.json` after responding; master polls with a second `podman exec`; cleaner stdout but extra round-trip on every turn.
3. *Shared volume scan* — master scans a designated output directory by timestamp after each turn; fragile to clock skew and file leftovers.

### Discord outbound size (tiered compression)

1. *Tiered fallback pipeline* — Nitro fast-path → already under cap → ZIP recompression + image downsampling → format conversion (docx→PDF via LibreOffice+Ghostscript, xlsx→CSV) → hard failure with workspace path notice.
2. *Temporary URL delivery* — short-lived HTTP endpoint or presigned S3 URL; high infrastructure complexity, out of scope for this iteration.
3. *Slack Files API as CDN, link posted to Discord* — cross-platform coupling, architectural smell; rejected.

## Decision Outcome

**Chosen options (accepted):**

- *Inbound:* Option 1 — server-side conversion in the master runtime, two-tier delivery (inline for small files, `podman cp` + Read-tool pointer for large files). No restriction on inbound file type — accept anything the platform delivers and pass it to the agent; the agent determines what to do with it. Unsupported types fall back gracefully with a notice.
- *Outbound:* Option 1 — JSON envelope in stdout, `claude-code` adapter only in v3.5. The `codex` adapter receives a clear "not supported" user-facing message rather than silently failing.
- *Conversion fidelity:* Best-effort for all types. For simple text + images, suggest that users send plain `.md` or `.txt` + separate image attachments for the clearest agent experience. Complex formatting (tracked changes, macros, embedded OLE objects) is extracted on a best-effort basis with no guarantee of structure preservation.
- *Discord size:* Option 1 — tiered fallback pipeline. Platform-aware logic lives exclusively in `discord_app.py`; Slack path is unaffected.
- *Scope:* Master-mode only (`src/master/`). `src/bot/` is not updated in this version.

### Consequences

- *Good:* Fully transparent to users — no pre-processing required. Reuses existing `podman cp` infrastructure. Context window overflow is eliminated by the staged-pointer approach.
- *Good:* Platform-specific compression logic is isolated to `discord_app.py`, consistent with the existing module split.
- *Good:* No inbound type restriction means zero friction regardless of what the user attaches.
- *Bad:* `send_prompt()` / `route_prompt()` return type changes from `str` to a dataclass — multiple call sites need updating.
- *Bad:* Codex users get a "not supported" message for outbound files; parity deferred to a future version.
- *Bad:* Format conversion (step 4 of Discord fallback) requires LibreOffice in the container image — presence must be verified at implementation time; if absent, this step is skipped and the pipeline falls through to the hard-failure notice.

### Confirmation

Design is confirmed. Implementation proceeds per the common workflow: engineer builds, tester validates interface stability, reviewer signs off.

## Constraint Reference

| Dimension | Slack inbound | Discord inbound | Slack outbound | Discord outbound |
|---|---|---|---|---|
| Max file size | 1 GB — *Platform* | 25 MB (Nitro: 500 MB) — *Platform* | 1 GB via files API — *Platform* | 25 MB (Nitro: 500 MB) — *Platform* |
| File types today | Images only — *Bot/Master* | Images + text/code extensions — *Bot/Master* | Text only | Text; >8 000 chars → `.md` file — *Bot/Master* |
| Inbound type restriction | None — accept all, agent decides | None — accept all, agent decides | N/A | N/A |
| Text attachment cap | No extraction today — *Solvable* | Hard 512 KB — *Bot/Master* (raise for this feature) | N/A | N/A |
| Context window overflow | Mitigated via staged-pointer for large files | Same | N/A | N/A |
| Discord CDN URL expiry | N/A | URLs expire ~1 hr — download-and-stage immediately | N/A | Bot reply links also expire — *Platform* |
| Outbound file return | claude-code adapter only | claude-code adapter only | Codex: "not supported" notice | Codex: "not supported" notice |
| Outbound compression | No size pressure | No size pressure | No size pressure | Tiered pipeline: recompress → convert → hard-fail notice |

*Legend:* Platform = enforced by Discord/Slack before the bot sees it. Bot/Master = our code. Agent = Claude Code model limit. Solvable = addressable in implementation.

## Implementation Notes

- **LibreOffice check**: at engineer time, verify `libreoffice --version` in the container. If absent, skip compression step 4 and go directly to hard-failure notice. Document the finding.
- **Suggested format hint**: when a user sends a complex docx, include in the agent prompt: *"For best results with text + images, send a `.md` or `.txt` file and attach images separately."*
- **`DispatchResult` dataclass**: `text: str`, `file_paths: list[Path]`. Empty list is the default — no behaviour change for turns without file output.
- **Codex "not supported" message**: injected by the master before routing, not inside the agent container. Message: *"File attachments in replies are not supported for this agent type. The agent can read your uploaded files but cannot return modified files as attachments."*
