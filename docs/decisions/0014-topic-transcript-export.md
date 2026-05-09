---
title: "ADR-0014: Topic transcript export as Markdown via backend endpoint"
status: accepted
date: 2026-05-09
decision-makers: [architect, engineer]
consulted: [tester, doc-writer]
informed: [sre, users]
---

## Context and Problem Statement

Users want to take topic conversations out of the app and into PRs, design
docs, issue write-ups, and incident retros. Today the only path is
copy-paste from the rendered chat, which loses agent reasoning, tool
calls, and tool output, and is tedious for long topics. We need a
single-click export that produces a faithful, portable artefact and is
also reachable from scripts and CLIs.

## Decision Drivers

- **Portability of the artefact.** The output must render natively in
  GitHub, GitLab, Notion, Obsidian, and any plain-text editor without
  custom tooling.
- **Fidelity.** Final agent text, agent thinking, tool calls, and tool
  results must all be present. Hiding reasoning behind collapsible
  sections keeps the file readable while preserving information.
- **Determinism.** Two exports of the same topic must produce
  byte-identical output (modulo a single export-timestamp line) so the
  artefact is diffable and reviewable.
- **Scriptability.** The export must be reachable from `curl`, CI, and
  external tooling — not just the SPA.
- **Minimum surface area.** Topics today are sub-megabyte; the
  implementation must not over-engineer for a future that may not arrive.
- **Reuse existing patterns.** The attachment download endpoint already
  defines how the codebase serves a file as a `Content-Disposition`
  attachment. The transcript shape already has a parser (the Vue
  `classifyEvent` function) we can mirror server-side.

## Considered Options

1. **Backend endpoint, Markdown only** — new FastAPI route renders the
   full conversation server-side and returns a `.md` file.
2. **Client-side export in the Vue SPA** — the browser assembles the
   Markdown from data already in memory.
3. **Background job + signed-URL delivery** — a worker renders the
   export asynchronously; the user gets a link or email when ready.
4. **Multi-format export from day one** — ship Markdown alongside HTML
   and/or JSON behind one endpoint.

## Decision Outcome

*Chosen option:* Option 1 — **backend endpoint, Markdown only** —
because it is the only option that delivers all four primary drivers
(portability, fidelity, determinism, scriptability) at once, and it
reuses the existing attachment-download pattern with no new
infrastructure. The endpoint accepts a `?format=` query parameter so
additional formats are an additive change later.

The export is triggered from a download icon in the topic toolbar,
placed next to the existing settings cog in `TopicChat.vue`.

### Consequences

- *Good:* one canonical, reproducible artefact. Same topic, same bytes,
  every time.
- *Good:* same surface for SPA, scripts, and future CLI tooling.
- *Good:* zero new infrastructure — pure FastAPI route, in-memory
  render, response stream identical in shape to attachment downloads.
- *Good:* readable on GitHub/GitLab out of the box (collapsible
  `<details>` sections for thinking and tool calls).
- *Bad:* in-memory render means a pathological multi-thousand-message
  topic could allocate tens of MB. Mitigated by a configurable
  `settings.export_max_bytes` cap (default 16 MiB) that returns 413.
- *Bad:* duplicates a small amount of transcript-parsing logic between
  the Vue renderer and the new server-side renderer. Acceptable: the
  parsing rules are short and stable, and the server renderer is the
  authoritative one for the export contract.
- *Bad:* commits us to Markdown's rendering quirks (HTML in
  `<details>` tags is required for the collapse behaviour). Renderers
  that strip raw HTML degrade gracefully — content is still present,
  just always-expanded.

### Confirmation

- Unit tests in `tests/test_topic_export.py` cover the renderer for
  every transcript shape (empty topic, agent with no transcript,
  thinking-only, tool-use without result, tool-use with result, mixed,
  attachments).
- Integration test against the FastAPI app verifies the response
  headers (`Content-Type`, `Content-Disposition`) and the 404/413/422
  status paths.
- UAT case in `docs/test-plans/topic-transcript-export.md` walks a
  human through clicking the toolbar button and inspecting the
  resulting file in GitHub's Markdown preview.

## Pros and Cons of the Options

### Option 1: Backend endpoint, Markdown only

A FastAPI route renders Markdown server-side and returns it as a
download.

- Pro: deterministic and reproducible across clients.
- Pro: scriptable — `curl` can fetch it without a browser.
- Pro: reuses the attachment-download response pattern.
- Pro: server has authoritative access to the full message list, no
  pagination concerns.
- Con: tiny duplication of transcript-parsing logic between server
  renderer and the Vue chat renderer.
- Con: in-memory render — needs a size cap to be safe.

### Option 2: Client-side export in the Vue SPA

The browser walks `messages.value` and assembles a Markdown blob, then
triggers a download via `URL.createObjectURL`.

- Pro: zero backend change.
- Pro: instant — no network round trip.
- Con: not scriptable; only works while a human has the SPA open.
- Con: output drifts between SPA versions, browsers, and
  locale/timezone settings — not a stable artefact.
- Con: depends on whatever subset of messages the SPA happens to have
  loaded. With pagination or virtual scrolling, partial exports become
  a real risk.
- Con: the export contract becomes coupled to the chat-view code path,
  which is otherwise free to evolve.

### Option 3: Background job + signed URL

A worker dequeues an export job, renders to object storage, and emails
or links to the result.

- Pro: handles arbitrarily large topics with no in-process memory cap.
- Pro: separates request latency from render latency.
- Con: requires worker infrastructure and object storage — the project
  has neither today.
- Con: user no longer gets one-click instant download; latency goes
  from milliseconds to "check your email".
- Con: massively overbuilt for current topic sizes (sub-MB).

### Option 4: Multi-format from day one (Markdown + HTML + JSON)

Ship `?format=md`, `?format=html`, `?format=json` together.

- Pro: avoids a follow-up ticket if a second format is requested.
- Pro: HTML preserves rendering more faithfully for some destinations.
- Con: triples the test surface for one feature.
- Con: JSON export is just `GET /messages` with a different
  `Content-Disposition`; adding it as a "format" muddies two
  endpoints' responsibilities.
- Con: pays the cost now for use cases that may never arrive. The
  `?format=` parameter on Option 1 keeps the door open without paying
  for it.
