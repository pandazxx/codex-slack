---
title: "ADR-0001: Rich file attachment support (inbound and outbound)"
status: proposed
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

**Chosen options (proposed):**

- *Inbound:* Option 1 — server-side conversion in the master runtime, two-tier delivery (inline for small files, `podman cp` + Read-tool pointer for large files). Threshold is a configurable constant.
- *Outbound:* Option 1 — JSON envelope in stdout. `ClaudeCodeDispatcher` already parses JSON from `claude --output-format json`; extending the envelope is low-friction. Return type changes from `str` to `DispatchResult(text, file_paths)`.
- *Discord size:* Option 1 — tiered fallback pipeline. Platform-aware logic lives exclusively in `discord_app.py`; Slack path is unaffected.

### Consequences

- *Good:* Fully transparent to users — no pre-processing required. Reuses existing `podman cp` infrastructure. Context window overflow is eliminated by the staged-pointer approach.
- *Good:* Platform-specific compression logic is isolated to `discord_app.py`, consistent with the existing module split.
- *Bad:* `send_prompt()` / `route_prompt()` return type changes from `str` to a dataclass — multiple call sites need updating.
- *Bad:* Format conversion (step 4 of Discord fallback) requires LibreOffice in the container image; presence needs verification.
- *Bad:* Agents must be instructed (via CLAUDE.md and system prompts) to declare output files in the JSON envelope — requires prompt discipline.

### Confirmation

Design is confirmed when: all open questions below are resolved, a design doc is produced in `docs/design/`, and the engineer agent implements to spec with passing tests.

## Constraint Reference

| Dimension | Slack inbound | Discord inbound | Slack outbound | Discord outbound |
|---|---|---|---|---|
| Max file size | 1 GB — *Platform* | 25 MB (Nitro: 500 MB) — *Platform* | 1 GB via files API — *Platform* | 25 MB (Nitro: 500 MB) — *Platform* |
| File types today | Images only — *Bot/Master* | Images + text/code extensions — *Bot/Master* | Text only | Text; >8 000 chars → `.md` file — *Bot/Master* |
| docx / xlsx / pdf | Silently discarded — *Solvable* | Silently discarded — *Solvable* | Not implemented — *Solvable* | Not implemented — *Solvable* |
| Text attachment cap | No extraction today — *Solvable* | Hard 512 KB — *Bot/Master* | N/A | N/A |
| Context window overflow | No guard; Claude errors at runtime — *Agent* (mitigable via staged-pointer) | Same | N/A | N/A |
| Discord CDN URL expiry | N/A | URLs expire ~1 hr — *Platform* (mitigable: download-and-stage immediately) | N/A | Bot reply links also expire — *Platform* |
| Outbound compression needed | No size pressure | No size pressure | No size pressure | >25 MB fails — *Solvable* via tiered pipeline |

*Legend:* Platform = enforced by Discord/Slack before the bot sees it. Bot/Master = our code. Agent = Claude Code model limit. Solvable = addressable in implementation.

## Open Questions

These must be resolved before the design doc is finalised and implementation begins.

1. **Adapter scope** — should outbound file return work for the `claude-code` adapter only, or also for the `codex` (PodmanExecDispatcher) adapter? The JSON envelope approach is simpler for `claude-code` since it already outputs structured JSON.

2. **Conversion fidelity** — for `.docx` files with complex formatting (tracked changes, embedded images, macros), server-side text extraction will lose structure. Is best-effort extraction acceptable, or does the agent need a closer representation (e.g. raw XML)?

3. **File types for phase 1** — implement all of `xlsx`, `csv`, `pdf`, `docx` at once, or prioritise a subset? Suggested priority: `xlsx`/`csv` (tabular, highest demand), then `pdf` (read-only), then `docx` (editable).

4. **Bot mode** — the `src/bot/` path is the older single-session Slack bot and has no file attachment handling. Should this feature target master-mode only, or must `src/bot/` also be updated?

5. **LibreOffice availability** — the Discord outbound compression fallback (step 4) requires LibreOffice in the agent container for docx→PDF conversion. Is LibreOffice present in the container image, or does it need to be added?
