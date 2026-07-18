---
title: "ADR-0016: Vue Flow + client-side parsing for the topic graph view"
status: accepted
date: 2026-07-04
decision-makers: [architect, engineer]
consulted: [user]
informed: [doc-writer, tester]
---

## Context and Problem Statement

We're adding an interactive graph view of a topic's conversation and transcript — a timeline flow that expands user/agent turns into their internal thinking / tool_use / tool_result / subagent subtrees. Two coupled decisions need to be locked in before implementation starts: (1) which graph library to build the view on, and (2) where the graph data model is produced — client-side from the existing `MessageOut[]` endpoint, or from a new backend endpoint.

## Decision Drivers

- Node contents are markdown, syntax-highlighted code, thinking blocks, and tool-use/result cards that we already render in `TopicChat.vue` via `MarkdownMessage.vue` and `highlight.js`. Reuse matters — reimplementing them in a foreign rendering pipeline is duplication we don't want to carry.
- Vue 3 + Vite. Whatever we pick must be idiomatic in this stack; wrappers around React libraries are a non-starter.
- v1 should ship without new backend work. We have `GET .../messages` returning full transcripts already.
- The graph data model needs to survive a future LLM summarization pipeline — meaning the shape must be documented and stable, whether produced client- or server-side.
- Performance target: interactive on topics up to a few thousand transcript events, without a bespoke virtualization layer.
- Time-to-first-usable-view — we want the design phase to end with a small library commitment, not a multi-week rendering-engine build.

## Considered Options

1. Vue Flow (`@vue-flow/core`) + client-side parser.
2. Cytoscape.js + client-side parser.
3. D3 + hand-rolled Vue integration + client-side parser.
4. Vue Flow + new backend graph endpoint (server-side parsing).

## Decision Outcome

*Chosen option:* Option 1 — Vue Flow with a client-side parser — because Vue Flow renders nodes as first-class Vue components (so we reuse `MarkdownMessage`, `highlight.js`, and existing trace-row styling directly), and client-side parsing avoids new backend surface for a feature whose data model is still shaking out. The graph IR is defined as a versioned, serializable shape so a future backend endpoint can produce the same output when it becomes worthwhile.

### Consequences

- *Good:* zero backend changes for v1 — new view ships purely against existing endpoints.
- *Good:* node components reuse markdown / code / badge rendering already written for chat view; the two views stay visually consistent by construction.
- *Good:* the graph IR (documented in the design doc) is the single contract between parser, renderer, and any future summary pipeline — it's easy to swap in a backend-produced graph later without touching node components.
- *Good:* Vue Flow ships panning, zooming, minimap, edge routing, and viewport culling — features we don't have to build.
- *Bad:* Vue Flow adds ~50KB gzipped to the frontend bundle. Mitigated by route-level code splitting so it loads only when the graph view is opened.
- *Bad:* client-side parsing scales with transcript size; very large topics (>5000 events) will need paging or a backend endpoint. Design specifies a truncation cap with a diagnostic; no user-visible impact expected in the near term.
- *Bad:* deterministic layout must be built ourselves (Vue Flow renders where we tell it). The design pins this as a small O(N) pure function — acceptable, and testable.

### Confirmation

Feature ships behind the `/workspaces/:wsId/topics/:topicId/graph` route. Tests: parser unit tests against the checked-in sample fixture, layout unit tests, and a smoke test that the view mounts and renders the sample topic. Reviewer checks that no node component re-implements markdown or highlight logic locally.

## Pros and Cons of the Options

### Option 1: Vue Flow + client-side parser

Vue-3-native graph library where nodes are custom Vue components; parser is a pure JS module in `frontend/src/lib/transcriptGraph.js`.

- Pro: node contents are Vue — direct reuse of `MarkdownMessage.vue` and existing styles
- Pro: no backend work; ships against existing `/messages` endpoint
- Pro: minimap, pan/zoom, edge routing, viewport culling all included
- Pro: IR is a documented shape — future backend endpoint or summary pipeline can produce it without changing the renderer
- Con: bundle size (mitigated by route-level splitting)
- Con: client CPU cost scales with transcript size; hard cap at 5000 events in v1

### Option 2: Cytoscape.js + client-side parser

Mature graph library with rich layout algorithms; DOM/canvas-driven rendering with HTML labels.

- Pro: mature, feature-rich, many built-in layouts
- Pro: performant on very large graphs
- Con: nodes are not Vue components — we'd either duplicate markdown/highlight rendering into Cytoscape HTML labels or maintain a Vue↔Cytoscape bridge (both are ongoing costs, not one-off)
- Con: the layout algorithms it excels at (force, cose, dagre) aren't what we need — timeline flow is a simple DFS layout
- Con: less idiomatic in a Vue 3 codebase; API surface is imperative

### Option 3: D3 + hand-rolled Vue integration

Build the renderer on D3 primitives; write our own node components, edge routing, pan/zoom, minimap.

- Pro: total flexibility; no library ceiling
- Pro: smallest possible bundle if minimized aggressively
- Con: the value D3 uniquely adds (force layouts, arbitrary custom visualizations) isn't relevant here
- Con: reimplementing pan/zoom, edge routing, minimap, viewport culling is multi-week work with ongoing maintenance
- Con: correctness bugs in hand-rolled interaction handling would land on us

### Option 4: Vue Flow + backend graph endpoint

Same rendering choice as Option 1, but parsing lives in `src/master/` and is exposed via a new `GET .../graph` endpoint.

- Pro: single canonical parse; server can cache
- Pro: paves the way for the summary pipeline to produce the graph directly
- Con: backend work for v1 while the IR shape is still being validated — every IR change becomes a schema migration
- Con: no user benefit today; parser is O(N) and cheap on the client
- Con: doesn't remove the client-side path — clients still need to parse in the interim, so this is additive complexity, not a replacement

Option 4 is the expected v2 optimization once the IR has stabilized and the summary pipeline exists to justify server-side aggregation. Deferring it is the whole point of shipping v1 client-side.

## Implementation Notes

- Library: `@vue-flow/core` plus `@vue-flow/minimap`. Route-level code split via dynamic `import()` in `frontend/src/main.js`.
- IR contract: documented in `docs/design/topic-graph-view.md` under "Graph IR". Versioned via `Graph.version = 1`.
- Client parser module: `frontend/src/lib/transcriptGraph.js`, pure function `buildTopicGraph(...) -> Graph`. Layout in a sibling `graphLayout.js`. Both testable in isolation, no Vue dependency.
- Summary attachment points defined now: `Graph.summaries` (topic-level), `Graph.messageSummaries[messageId]` (per message), `GraphNode.summaries` (per node). All empty in v1. A future backend pipeline populates them without any change to node components.
