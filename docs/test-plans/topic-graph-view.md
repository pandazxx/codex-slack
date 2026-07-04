# Test Plan: Topic Graph View

**Feature design:** [docs/design/topic-graph-view.md](../design/topic-graph-view.md)
**Schema reference:** [docs/references/schemas/topic-transcript-events.md](../references/schemas/topic-transcript-events.md)
**Fixtures:** `tests/fixtures/topic-graph/`
**Test files (to be created during implementation):**
- `frontend/src/lib/__tests__/transcriptGraph.test.js` — parser unit tests (vitest)
- `frontend/src/lib/__tests__/graphLayout.test.js` — layout unit tests (vitest)

vitest is already present in `frontend/package.json` devDependencies. Test bodies against application code wait until `frontend/src/lib/transcriptGraph.js` lands (step 6 of feature workflow). This plan plus fixtures is the step-4 scaffolding.

---

## Scope

The parser contract under test is:

```js
buildTopicGraph({ workspaceId, topicId, topicSubject, workspaceName, messages, generatedAt }) -> Graph
```

Input: `MessageOut[]` (fetched from `GET /api/workspaces/{wsId}/topics/{topicId}/messages`), shaped as described in the schema reference. Output: the `Graph` IR defined in the design doc.

Secondary scope: `layoutGraph(graph, options) -> Graph` (adds `ui.x`, `ui.y` to nodes); route existence and toggle navigation; node component rendering; keyboard navigation; deep-link handling.

Out of scope for this plan: the LLM summarization pipeline; live WebSocket updates; mobile layout; backend graph endpoint.

---

## Node taxonomy reference

| `NodeKind` | Source event(s) |
|---|---|
| `topic` | Synthetic root, one per graph |
| `user-message` | `type: "user"` top-level record |
| `agent-message` | `type: "agent"` top-level record |
| `result-rollup` | `result/success` |
| `thinking` | `assistant` content block `type: "thinking"` |
| `text` | `assistant` content block `type: "text"` |
| `tool-use` | `assistant` content block `type: "tool_use"` |
| `tool-result` | `user` event content block `type: "tool_result"` |
| `subagent` | Synthetic, one per `Agent` tool_use |
| `task-event` | `system/task_started`, `system/task_progress`, `system/task_notification` |
| `rate-limit` | `rate_limit_event` |
| `system-init` | `system/init` |
| `parse-warning` | Malformed / unrecognised event |

---

## Happy Path

### HP-01 Empty topic
- **Fixture:** none (inline in test)
- **Input:** `messages = []`
- **Assertions:**
  - Graph contains exactly 1 node with `kind: "topic"`.
  - `nodes.length === 1`; `edges.length === 0`.
  - `graph.summaries === []`; `graph.messageSummaries === {}`; `graph.diagnostics === []`.
  - `graph.version === 1`.
- **UAT:** `automated`

### HP-02 Text-only topic — spine and inner nodes
- **Fixture:** `simple-text.jsonl`
- **Input:** 2 user + 2 agent records; model override `claude-opus-4-7`; `is_new_session` true then false.
- **Assertions:**
  - 1 `topic` node + 2 `user-message` nodes + 2 `agent-message` nodes on the spine. 5 spine nodes total.
  - First `user-message` node: `data.dispatch.is_new_session === true`; `data.dispatch.model === "claude-opus-4-7"`.
  - Second `user-message` node: `data.dispatch.is_new_session === false`.
  - First `agent-message` subtree: 1 `system-init` + 1 `rate-limit` + 1 `thinking` + 1 `text` + 1 `result-rollup`. Total inner nodes: 5.
  - Second `agent-message` subtree: 1 `rate-limit` + 1 `thinking` + 1 `text` + 1 `result-rollup`. Total inner nodes: 4.
  - `system-init` node `data.model === "claude-opus-4-7"`.
  - `agent-message` node `data.model === "claude-opus-4-7"` (extracted from `system/init`).
  - Every node has a non-null `id`; all `id` values are unique.
  - `sequence` values are strictly increasing across all nodes in DFS order.
  - All `summaries` arrays are `[]`.
  - `diagnostics === []`.
- **UAT:** `automated`

### HP-03 Tool use / tool result pairing — Glob
- **Fixture:** `tools-and-subagent.jsonl`
- **Input:** main-thread `Glob` tool_use (id `toolu_glob_001`) + matching `tool_result`.
- **Assertions:**
  - A `tool-use` node with `data.toolName === "Glob"` and `data.toolUseId === "toolu_glob_001"` exists.
  - A `tool-result` node with `data.toolUseId === "toolu_glob_001"` exists.
  - An `invokes` edge from the `tool-use` node to the `tool-result` node exists.
  - The `tool-result` node's `parentId` equals the `tool-use` node's `id`.
- **UAT:** `automated`

### HP-04 Subagent subtree attachment
- **Fixture:** `tools-and-subagent.jsonl`
- **Input:** `Agent` tool_use (id `toolu_agent_001`) spawning Explore subagent.
- **Assertions:**
  - A `tool-use` node with `data.toolName === "Agent"` exists.
  - A `subagent` node exists with `parentId` equal to the `tool-use` node's `id`.
  - An `invokes` edge from the `tool-use` node to the `subagent` node exists.
  - All events with `parent_tool_use_id === "toolu_agent_001"` (3 `assistant` events, 2 `user` events in the fixture) produce nodes whose `parentId` traces back to the `subagent` node.
  - `system/task_started` → `task-event` node is a child of the `subagent` node.
  - `system/task_progress` → `task-event` node is a child of the `subagent` node.
  - `system/task_notification` → `task-event` node is a child of the `subagent` node.
  - Subagent-scoped `assistant` content blocks (model `claude-haiku-4-5-20251001`) produce `thinking`, `text`, `tool-use` nodes as children of the `subagent` node.
  - The Agent `tool-result` (main-thread rollup) is a child of the `tool-use` node, not of the `subagent`.
  - `subagent` node `data.agentType` is populated from `task_started.subagent_type`.
- **UAT:** `automated`

### HP-05 result/success rollup node
- **Fixture:** `simple-text.jsonl`
- **Input:** `result/success` event at end of each agent message.
- **Assertions:**
  - Each `agent-message` subtree contains exactly 1 `result-rollup` node.
  - `result-rollup` node `data.isError === false`.
  - `result-rollup` node `data.cost` matches `total_cost_usd` from the source event.
  - `result-rollup` node `data.durationMs` matches `duration_ms`.
  - `result-rollup` node `data.numTurns` matches `num_turns`.
- **UAT:** `automated`

### HP-06 system/init node and model extraction
- **Fixture:** `simple-text.jsonl`
- **Input:** `system/init` event in first agent message.
- **Assertions:**
  - A `system-init` node exists as child of the first `agent-message`.
  - `system-init` node `data.model` matches the `model` field from the `system/init` event.
  - The parent `agent-message` node `data.model` also equals this value.
- **UAT:** `automated`

### HP-07 Determinism
- **Fixture:** `simple-text.jsonl` (or any fixture)
- **Input:** same `messages` array passed twice.
- **Assertions:**
  - `JSON.stringify(graph1) === JSON.stringify(graph2)`.
  - All `node.id` values are identical across both runs.
  - `generatedAt` field must be passed in by the caller and not use `Date.now()` internally.
- **UAT:** `automated`

---

## Background Tasks and Compaction

### BC-01 MCP tool call
- **Fixture:** `background-and-compaction.jsonl`
- **Input:** `mcp__notes__list_workspace_notes` tool_use + matching tool_result.
- **Assertions:**
  - A `tool-use` node with `data.toolName === "mcp__notes__list_workspace_notes"` exists.
  - Matching `tool-result` node exists with `data.toolUseId` pointing to the MCP call.
  - `invokes` edge between them.
  - No diagnostic is emitted for this tool_use.
- **UAT:** `automated`

### BC-02 Background Bash task lifecycle — task_updated patches
- **Fixture:** `background-and-compaction.jsonl`
- **Input:** `system/task_updated` with `{is_backgrounded: true}` then `{status: "failed", end_time: ...}`.
- **Assertions:**
  - Two `task-event` nodes with `data.subtype === "task_updated"` exist (or the parser merges them — confirm expected behavior in implementation).
  - These nodes attach to the correct `tool-use` subtree (Bash tool_use id `toolu_bash_bg_001`).
  - No diagnostic is emitted for these events.
- **UAT:** `automated`

### BC-03 is_error tool_result
- **Fixture:** `background-and-compaction.jsonl`
- **Input:** `tool_result` with `is_error: true` for the Bash task.
- **Assertions:**
  - The `tool-result` node's `data.isError === true`.
  - No diagnostic is emitted (this is a valid, expected result shape).
- **UAT:** `automated`

### BC-04 Context compaction events
- **Fixture:** `background-and-compaction.jsonl`
- **Input:** `system/status {status: "compacting"}` then `{status: null, compact_result: "success"}` then `system/compact_boundary`.
- **Assertions:**
  - The parser does not crash on `system/status` events.
  - The parser does not crash on `system/compact_boundary` events.
  - These events map to node kinds as specified (or are folded into `task-event` / `parse-warning` — confirm during implementation).
  - No spurious diagnostics are emitted.
- **UAT:** `automated`

---

## Interrupted and Edge Cases

### IE-01 Interrupted agent message — transcript null
- **Fixture:** `interrupted-and-edge.jsonl`
- **Input:** agent record with `transcript: null` and `text: "(message interrupted)"`.
- **Assertions:**
  - An `agent-message` node is emitted for this record.
  - `data.hasTranscript === false`.
  - `data.interruptReason` is set (non-null).
  - No `result-rollup` child exists for this node.
  - No diagnostic is emitted (null transcript is an expected state, not an error).
  - Subsequent records are still parsed; the graph is not truncated.
- **UAT:** `automated`

### IE-02 Consecutive user records
- **Fixture:** `interrupted-and-edge.jsonl`
- **Input:** two `user` records back-to-back (interrupt "stop" followed by continuation).
- **Assertions:**
  - Two `user-message` nodes appear as consecutive children of the `topic` root.
  - Each has its own distinct `id`.
  - `sequence` values are monotonically increasing across both.
- **UAT:** `automated`

### IE-03 Double system/init in one agent message (session restart)
- **Fixture:** `interrupted-and-edge.jsonl`
- **Input:** agent message with two `system/init` events (same `session_id`).
- **Assertions:**
  - Two `system-init` nodes are produced as children of the `agent-message`.
  - Both have distinct `id` values.
  - `agent-message` `data.model` is set from the last (or first) `system/init` — confirm tie-break rule during implementation.
- **UAT:** `automated`

---

## Nested Subagent (Hypothetical)

### NS-01 Two-level subagent chain
- **Fixture:** `nested-subagent.jsonl` (marked hypothetical — not observed in production)
- **Input:** `toolu_agent_top` spawns coordinator subagent; coordinator contains `toolu_agent_api` spawning specialist subagent.
- **Assertions:**
  - Three levels of subagent nesting: `agent-message` → `subagent(toolu_agent_top)` → `subagent(toolu_agent_api)`.
  - Events with `parent_tool_use_id === "toolu_agent_top"` are children of the first `subagent` node.
  - Events with `parent_tool_use_id === "toolu_agent_api"` are children of the second `subagent` node.
  - `task-event` nodes for `task-coord-001` are children of the first `subagent`.
  - `task-event` nodes for `task-api-audit-001` are children of the second `subagent`.
  - `result-rollup` for the outer message attaches to the `agent-message` spine node.
  - No diagnostic is emitted.
- **UAT:** `automated`

---

## Failure Modes and Diagnostics

### FM-01 Orphan tool_result — unknown tool_use_id
- **Fixture:** `malformed.jsonl`
- **Input:** `tool_result` with `tool_use_id: "toolu_unknown_999"` which was never emitted as a `tool_use`.
- **Assertions:**
  - A `Diagnostic` with `code: "orphan_tool_result"` is present in `graph.diagnostics`.
  - `diagnostic.messageId` is set to the `agent-message` node's `messageId`.
  - A `tool-result` node is still emitted (attached to the parent `agent-message` as a fallback — the parser must not discard it).
  - All other nodes in the message are still produced correctly.
- **UAT:** `automated`

### FM-02 Orphan tool_use — tool_use with no matching tool_result
- **Fixture:** `malformed.jsonl`
- **Input:** `tool_use` with `id: "toolu_orphan_001"` that has no `tool_result` counterpart.
- **Assertions:**
  - A `tool-use` node for `toolu_orphan_001` is produced.
  - No `invokes` edge from it to a `tool-result` exists.
  - A `Diagnostic` with `code: "orphan_tool_use"` is present (or the parser silently accepts this — confirm policy during implementation; either way must be documented).
- **UAT:** `automated`

### FM-03 Unknown event type
- **Fixture:** `malformed.jsonl`
- **Input:** top-level event with `type: "future_event"`.
- **Assertions:**
  - A `Diagnostic` with `code: "unknown_event_type"` is present in `graph.diagnostics`.
  - `diagnostic.eventIndex` is set to the correct index within the agent message's transcript.
  - No node is emitted for this event (or a `parse-warning` node is emitted — confirm policy).
  - Parsing of subsequent events continues; `result-rollup` and `text` nodes in the same message are still produced.
- **UAT:** `automated`

### FM-04 Unknown assistant content block type
- **Fixture:** `malformed.jsonl`
- **Input:** `assistant` event with a content block of type `unknown_content_block_type`.
- **Assertions:**
  - Parser does not throw.
  - The unknown block is either silently skipped or a `parse-warning` node is emitted.
  - Known blocks in the same `assistant` event (`thinking`, `tool_use`) are still processed correctly.
- **UAT:** `automated`

### FM-05 task_started with no matching subagent in index
- **Fixture:** (inline in test — synthesize a `task_started` event referencing a `tool_use_id` that was never emitted as an `Agent` tool_use)
- **Assertions:**
  - A `Diagnostic` with `code: "orphan_task_event"` (or similar) is emitted.
  - The `task-event` node is attached to the parent `agent-message` as a fallback.
- **UAT:** `automated`

### FM-06 topic exceeds 5000-event hard cap
- **Fixture:** (inline in test — synthesize a synthetic `messages` array whose total event count exceeds 5000)
- **Assertions:**
  - `graph.diagnostics` contains a `Diagnostic` with `code: "over_size"`.
  - Events beyond the cap are not present in the graph.
  - Spine nodes (`user-message`, `agent-message`) are all present even if their inner events are truncated.
- **UAT:** `automated`

---

## Layout

### LY-01 All spine nodes have x=0, inner nodes have x > 0
- **Fixture:** `simple-text.jsonl` parsed graph
- **Assertions:**
  - All `user-message` and `agent-message` nodes have `ui.x === 0`.
  - All `thinking`, `text`, `tool-use`, `tool-result` nodes have `ui.x === 320` (one `colWidth` deep).
  - `subagent` nodes inside a `tool-use` have `ui.x === 640`.
- **UAT:** `automated`

### LY-02 y values are monotonically increasing
- **Fixture:** `simple-text.jsonl` parsed graph
- **Assertions:**
  - For all visible nodes, `sequence[i] < sequence[j]` implies `ui.y[i] <= ui.y[j]` (non-strictly — siblings at the same depth can share the same y increment step).
- **UAT:** `automated`

### LY-03 Collapsed subtree hides inner nodes
- **Fixture:** `tools-and-subagent.jsonl` parsed graph — set `agent-message.ui.collapsed = true`
- **Assertions:**
  - After `layoutGraph`, all child nodes of the collapsed `agent-message` have `ui.hidden === true`.
  - The collapsed `agent-message` node itself remains visible.
- **UAT:** `automated`

### LY-04 Deterministic layout
- **Fixture:** any
- **Assertions:**
  - Running `layoutGraph` twice on the same graph (same collapsed state) produces identical `ui.x` and `ui.y` for all nodes.
- **UAT:** `automated`

---

## Non-Functional Requirements

### NF-01 Performance — 2000 events parsed within 100ms
- **Method:** construct a synthetic `messages` array with ~2000 total events (20 agent messages, ~100 events each); measure wall-clock time of `buildTopicGraph(...)`.
- **Assertion:** elapsed time < 100ms (vitest `performance.now()`).
- **UAT:** `automated`

### NF-02 No Vue imports in parser
- **Method:** static check — `grep -E "^import.*from 'vue'" frontend/src/lib/transcriptGraph.js` must return nothing.
- **Assertion:** exit code 1 (no matches).
- **UAT:** `automated`

### NF-03 Summary slots always empty in v1 parser
- **Fixture:** any
- **Assertions:**
  - `graph.summaries.length === 0`.
  - Every value in `graph.messageSummaries` is an empty array.
  - Every `node.summaries.length === 0` across all nodes.
- **UAT:** `automated`

### NF-04 All node IDs are stable and unique
- **Fixture:** any
- **Assertions:**
  - `new Set(graph.nodes.map(n => n.id)).size === graph.nodes.length` (no duplicate IDs).
  - Re-parsing the same input produces the same set of IDs (stability).
- **UAT:** `automated`

---

## View and Interaction (UAT)

### VI-01 Graph route loads and renders spine nodes
- **Method:** navigate to `/workspaces/:wsId/topics/:topicId/graph` in the dev env; verify the graph view mounts without error.
- **Expected:** spine nodes visible for at least the first 3 user/agent messages; no console errors.
- **UAT:** `needs-human` — requires a live dev env with a real topic loaded.

### VI-02 Chat/Graph toggle navigates between views
- **Method:** start on `/workspaces/:wsId/topics/:topicId`; click the Graph button in the header toggle.
- **Expected:** URL changes to `.../graph`; graph view renders. Clicking Chat navigates back; chat view renders.
- **UAT:** `needs-human`

### VI-03 Expand/collapse agent message subtree
- **Method:** in graph view, click the chevron on an agent-message node.
- **Expected:** inner nodes disappear (collapse); click again — inner nodes reappear (expand). Layout re-runs; no overlap or jump in other nodes.
- **UAT:** `needs-human`

### VI-04 Node detail panel
- **Method:** click any node in the graph view.
- **Expected:** a detail panel opens on the right (~380px). Content matches the node's kind (tool-use shows input JSON; thinking shows purple-tinted text; subagent shows agentType, prompt, usage). Pressing `Esc` closes the panel.
- **UAT:** `needs-human`

### VI-05 Keyboard navigation
- **Method:** with a node selected, press `→` and `←`.
- **Expected:** selection moves to next/previous node by `sequence` order. `Space` toggles collapsed state of the selected node.
- **UAT:** `needs-human`

### VI-06 Deep-link to a graph node via URL query param
- **Method:** navigate to `/workspaces/:wsId/topics/:topicId/graph?node=<nodeId>` where `<nodeId>` is a known `tool-use` node ID.
- **Expected:** the graph loads with that node selected; the detail panel opens automatically.
- **UAT:** `needs-human`

### VI-07 Minimap toggle persists in localStorage
- **Method:** toggle the minimap off via the header button; reload the page.
- **Expected:** minimap remains hidden after reload. Toggle it on; reload — minimap visible.
- **UAT:** `needs-human`

### VI-08 Graph view on topic with interrupted message
- **Method:** load a topic that has a message with `transcript: null` in the graph view.
- **Expected:** the interrupted agent-message node appears on the spine with an "interrupted" visual cue (amber color per design). No crash. The next messages render normally.
- **UAT:** `needs-human`

### VI-09 Diagnostics surface to user
- **Method:** load a topic whose log contains an orphan `tool_result` or unknown event type (use the `malformed.jsonl` fixture content, or a real topic if available in the dev env).
- **Expected:** a "3 warnings" (or similar count) indicator is visible in the graph header or topic panel. Clicking it shows the diagnostic details.
- **UAT:** `needs-human`

### VI-10 "Open in chat" link from graph node
- **Method:** select a `user-message` or `agent-message` node; click the "→ chat" affordance in the detail panel.
- **Expected:** a new tab (or navigation) opens `/workspaces/:wsId/topics/:topicId#msg-<id>`; the chat view scrolls to and briefly highlights that message.
- **UAT:** `needs-human`

---

## Schema Ambiguities Noted During Fixture Authoring

1. **`task_updated` node kind.** The schema lists `system/task_updated` as an observed event but the design's node taxonomy does not have a dedicated `task-updated` kind — it is grouped under `task-event`. The fixture uses `task_updated` events; the test plan assumes they produce `task-event` nodes. The implementation may merge all `system/task_*` subtypes under `task-event` or split them — confirm during step 5 and update FM-02 accordingly.

2. **`system/status` and `system/compact_boundary` node mapping.** The design lists these as events the parser must handle but does not assign them explicit `NodeKind` values. They are not in the taxonomy table. Options: map to `task-event`, map to a new `compaction-event` kind, or skip (emit a diagnostic). Confirm mapping during implementation; update BC-04 with the decided kind.

3. **Orphan tool_use diagnostic.** The design spec says parsers must "tolerate orphans defensively" but only names `orphan_tool_result` as a diagnostic code. FM-02 notes this ambiguity. If the implementation is intentionally silent on orphan `tool_use`, document it and remove FM-02's assertion on diagnostic emission.

4. **`subagent_type` field observed as `null` in real data for `local_agent` tasks.** The schema reference says `system/task_started` for `local_agent` has a `subagent_type` field; observed real data shows it as `null` while the `subagent_type` appears in the same event at top level (e.g. `Explore`). The `tools-and-subagent.jsonl` fixture models it as non-null at top level. The parser should read from `event.subagent_type` at the event root, not from a nested structure.

5. **model field on `agent-message` when multiple `system/init` events exist.** The design says model is "drawn from `system/init` when present" but does not specify a tie-break when there are two. The `interrupted-and-edge.jsonl` fixture has two `system/init` events with the same model, so this is not exercised for the conflict case. If a future fixture needs different models per init, this rule must be made explicit.
