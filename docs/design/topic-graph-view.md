# Design: Topic Graph View

**Status:** accepted
**Author:** architect
**Date:** 2026-07-04
**Related ADRs:** [ADR-0016](../decisions/0016-topic-graph-view.md); builds on ADR-0012 (Streaming agent reply).

## Context

The current topic view (`frontend/src/views/TopicChat.vue`) renders a linear chat log. Each agent message expands into an optional flat trace of Claude Code stream events (`assistant`, `user/tool_result`, `system/task_*`, `result/*`). For short exchanges this is fine; for long topics with many tool calls and subagent spawns the trace becomes a long scroll of monospace text where structure — which tool_use produced which tool_result, which subagent hangs off which Task tool_use, which turn a result summarizes — is not visible.

Users want a complementary view of the same data that surfaces this structure graphically: a timeline flow where the vertical spine is the user/agent turn sequence, and each agent turn can be expanded to reveal its internal thinking / tool_use / tool_result / subagent subtree. The graph view is read-only and reuses the existing messages endpoint.

The authoritative input contract for the parser is [`docs/references/schemas/topic-transcript-events.md`](../references/schemas/topic-transcript-events.md), derived from analysis of four real topic logs. Any shape not documented there is treated by the parser as unknown and routed through the diagnostics path — this doc's data model tracks that schema.

A later phase will add an LLM-driven summarization pipeline that produces summary artifacts (topic-level, message-level, potentially per-node) to be rendered as overlays or side panels in this same view. The v1 data model must define attachment points for these artifacts so the pipeline can produce them without further schema churn.

## Goals

- Sibling route `/workspaces/:wsId/topics/:topicId/graph` with a chat/graph toggle in the topic header. Toggle does not change the URL of the other view; each view has its own URL.
- Timeline flow layout: vertical chronological spine of user/agent message nodes; each agent message expands into a subtree of inner nodes.
- Every event kind classified by `classifyEvent()` in `TopicChat.vue` maps to a typed graph node, so no data is silently dropped.
- Subagent (Agent tool) subtrees rendered as children of their spawning `tool_use` node, inferred from `parent_tool_use_id`.
- Pure client-side v1: reuse `GET /api/workspaces/{wsId}/topics/{topicId}/messages`, parse the returned `MessageOut[]` into a graph IR at load time.
- Detail card per node reuses existing rendering primitives (`MarkdownMessage.vue`, `highlight.js`, badge styles from `TopicChat.vue`).
- Performance: acceptable interaction on topics up to ~2000 events (rough upper bound based on today's largest topics being a few hundred events per message).
- Extensibility: graph IR is a documented, serializable shape with well-defined attachment slots for future summary artifacts. A future backend endpoint can emit the same shape.

## Non-Goals

- Live WebSocket updates to the graph view. v1 is static — the view snapshots the topic at load time. A "Refresh" button is acceptable; incremental patching from `chunk` / `message` events is a documented follow-up.
- The LLM summarization pipeline itself. This design only defines where its output attaches. The pipeline is a separate feature.
- Cross-topic graphs, workspace-level graphs, or comparing topics.
- Editing / annotating / re-running from the graph. Read-only.
- Mobile-first layout. Graph is desktop-optimized; on narrow viewports the toggle falls back to chat.
- Backend graph endpoint. Documented as an extension point; not built in v1.
- Full-text search inside the graph. Out of scope; users search in chat view.

## Design

### 1. Route and toggle

Add the new route to `frontend/src/main.js`:

```js
import TopicGraph from './views/TopicGraph.vue'
// ...
{ path: '/workspaces/:wsId/topics/:topicId/graph', component: TopicGraph },
```

In both `TopicChat.vue` and `TopicGraph.vue`, render a small segmented toggle in the header row (near the settings gear):

```
[ Chat | Graph ]
```

Clicking `Graph` in chat view calls `router.push` to the `/graph` sibling route; clicking `Chat` in graph view goes back. The two views share no state — each fetches messages on mount. This keeps the graph view isolable and testable.

### 2. Graph IR (intermediate representation)

The IR is the single documented contract between:

- the client-side parser module (`frontend/src/lib/transcriptGraph.js`)
- `TopicGraph.vue` and its custom node components
- (future) a backend graph endpoint or summarization pipeline that produces the same shape

All IDs are stable strings — either derived from the source data (message.id, event tool_use_id) or deterministically hashed from `(message.id, event_index)` so re-parsing the same input yields the same IDs.

```ts
// Types shown as TS for clarity; implemented in plain JS.

type NodeKind =
  | 'topic'              // implicit root — carries topic-level metadata & summary slot
  | 'user-message'       // one per user MessageOut
  | 'agent-message'      // one per agent MessageOut (spine node)
  | 'result-rollup'      // synthetic — the `result/success` for an agent message; hangs off agent-message
  | 'thinking'           // one per assistant event carrying a thinking block
  | 'text'               // one per assistant event carrying a text block (agent's incremental prose)
  | 'tool-use'           // one per assistant tool_use block; carries name + input
  | 'tool-result'        // one per user tool_result block; linked to its tool-use by tool_use_id
  | 'subagent'           // synthetic container for a subagent invocation — wraps the child subtree
  | 'task-event'         // system/task_started | task_progress | task_notification | task_updated
  | 'compaction'         // system/compact_boundary (folds transient system/status compacting events)
  | 'system-init'        // system/init (usually collapsed)
  | 'parse-warning'      // malformed transcript line (defensive)

interface GraphNode {
  id: string
  kind: NodeKind
  parentId: string | null   // graph structural parent (topic > agent-message > subagent > tool-use, etc.)
  messageId: string | null  // MessageOut.id this node belongs to (null only for the topic root)
  sequence: number          // monotonic 0-based index in DFS order of the source events; used for stable layout & sort
  ts: string | null         // ISO timestamp when known (message.created_at, event ts if present, else null)

  // Kind-specific payload. Union in TS; a plain object with a `kind` discriminator in JS.
  data: NodeData

  // Presentation state (mutable at runtime, not persisted).
  ui: {
    collapsed: boolean      // whether the subtree is folded
    x?: number              // computed by layout; not authored by parser
    y?: number
  }

  // Summary-artifact attachment slot. v1 parser always emits [].
  // Future pipeline populates this without changing anything else.
  summaries: SummaryArtifact[]
}

interface GraphEdge {
  id: string                // `${sourceId}->${targetId}[:kind]`
  source: string
  target: string
  kind: EdgeKind
}

type EdgeKind =
  | 'contains'   // structural parent → child (agent-message → tool-use, subagent → assistant, ...)
  | 'invokes'    // tool-use → tool-result (or tool-use(Agent) → subagent)
  | 'follows'    // sibling ordering hint for renderers (optional; usually derivable from sequence)
  | 'summarizes' // summary artifact node (v2) → target node

interface Graph {
  topicId: string
  workspaceId: string
  generatedAt: string       // ISO — when the IR was built
  version: number           // IR schema version; v1 = 1
  nodes: GraphNode[]
  edges: GraphEdge[]
  // Topic-level summary slot. Populated by future pipeline.
  summaries: SummaryArtifact[]
  // Per-message summary slot, keyed by MessageOut.id. Same reason — future pipeline.
  messageSummaries: Record<string, SummaryArtifact[]>
  // Parser diagnostics for the UI (unknown event kinds, tool_result orphans, etc.).
  diagnostics: Diagnostic[]
}

interface SummaryArtifact {
  id: string
  kind: 'summary' | 'fact' | 'decision' | 'todo' | 'error' | string  // open enum; unknown kinds render generically
  title: string
  body: string              // markdown; rendered via MarkdownMessage
  producedBy: string        // e.g. 'llm:claude-haiku-4-5', 'heuristic:v1', 'user'
  producedAt: string        // ISO
  scope:
    | { type: 'topic' }
    | { type: 'message', messageId: string }
    | { type: 'node', nodeId: string }
  refs?: { nodeId: string }[]  // additional cross-links; renderer may draw dashed edges
}

interface Diagnostic {
  level: 'warn' | 'error'
  code: string              // e.g. 'orphan_tool_result', 'orphan_tool_use', 'orphan_task_event', 'unknown_event_type'
  message: string
  messageId?: string
  eventIndex?: number
}
```

Kind-specific `data` payloads (the parser copies the minimum needed for rendering; consumers can still reach for `messageId` + `sequence` to look up raw material if needed):

- `topic`: `{ subject, workspaceName }`
- `user-message`: `{ text, attachments, dispatch }` — `dispatch` is the parsed dispatch payload (adapter, agent_name, subagent, model, session_id, etc.) as `TopicChat.vue`'s `parseDispatch` produces.
- `agent-message`: `{ agentName, text, silent, interrupted, interruptReason, model, hasTranscript }` — `text` is the final message text (`"(message interrupted)"` when interrupted). `interrupted` is true iff the source record's `transcript` is `null`; in that case no inner nodes and no `result-rollup` child are emitted. `model` is drawn from `system/init` when present; when a message contains multiple `system/init` events (session restart), the **last** one wins.
- `result-rollup`: `{ isError, cost, durationMs, numTurns, usage }` — extracted from the `result/success` event. Absent for interrupted messages.
- `thinking`: `{ text }` — the `thinking` block string.
- `text`: `{ text }` — the assistant text block.
- `tool-use`: `{ toolName, input, toolUseId, label, mcpServer?, mcpTool? }` — `label` is what `toolUseLabel()` in `TopicChat.vue` would render (kept centralized in the parser to avoid Vue components computing it). For MCP tools whose `toolName` matches `mcp__<server>__<tool>`, the parser splits the parts into `mcpServer` and `mcpTool` so the node card can badge them independently; `toolName` is preserved verbatim.
- `tool-result`: `{ toolUseId, contentText, isError, agentType?, totalDurationMs?, totalTokens?, totalToolUseCount? }` — `isError` mirrors `tool_result.is_error` (defaulting to `false`). The `agentType` and derived counters come from `tool_use_result` when this is a Task result. **Compact-view folding (#249):** when a `tool-result` is the *sole* child of its `tool-use`, the parser folds it into the tool-use as `data.result` (same payload) and emits no separate node; results stay separate nodes when the tool-use has other children (subagent, task events) or is an orphan.
- `subagent`: `{ agentType, prompt, model?, summary? }` — synthesized from the sibling `system/task_*` events keyed by the same `tool_use_id`. Wraps all events whose `parent_tool_use_id` equals this id. Only created for `Agent` tool_uses (`task_type: "local_agent"`); `local_bash` tasks do **not** get a `subagent` container — their `task-event` nodes attach directly to the spawning `Bash` tool_use.
- `task-event`: `{ subtype, taskId, taskType, description, subagentType?, prompt?, toolUseId, patch?, status?, endTime?, usage?, isBackgrounded? }` — covers all `system/task_*` subtypes including `task_updated`. `taskType` is `"local_agent"` or `"local_bash"` (from `task_started`, propagated to later events by `taskId`). `subagent_type` and `prompt` live at the event root of `task_started` (not nested) and are only populated for `local_agent`. `patch` is copied verbatim from `task_updated` events; `status`, `endTime`, `isBackgrounded` are convenience projections of common patch keys for renderers that don't want to introspect the raw patch.
- `compaction`: `{ trigger, preTokens, postTokens, durationMs, compactResult? }` — one node per `system/compact_boundary` event, attached to the enclosing `agent-message`. Any `system/status` events with `status: "compacting"` / `compact_result` **fold into** the following `compact_boundary` node (they do not produce their own nodes). If a `system/status` compacting event has no following `compact_boundary` in the same message (partial data), the parser emits a `compaction` node with whatever fields it has and `compactResult` populated from the status event.
- `system-init`: `{ model, tools?, cwd?, sessionId? }` — a message may contain more than one; each produces its own node.
- `parse-warning`: `{ line }`.

**Why this shape:**

- Structural parent (`parentId`) is separate from `messageId` — a `tool-use` sits inside an `agent-message`, but a subagent's `assistant/text` sits inside a `subagent` which sits inside a `tool-use`. Rendering needs both.
- `sequence` is a scalar ordering key. Layout and detail-panel navigation ("next node") work on this without a graph traversal.
- `summaries`, `messageSummaries`, and the top-level `Graph.summaries` are three explicit attachment points. v1 always writes empty. A future pipeline appends to them; no other node types or edges are needed to represent summaries. Cross-references between a summary and additional nodes use `refs` and can be rendered as dashed `summarizes` edges when the user hovers a summary card.
- `Diagnostic[]` gives the UI a place to show "3 orphan tool_results were skipped" rather than silently mis-rendering.

### 3. Parser module — `frontend/src/lib/transcriptGraph.js`

Pure function, no Vue imports:

```js
// buildTopicGraph({ workspaceId, topicId, topicSubject, workspaceName, messages }) -> Graph
export function buildTopicGraph({ workspaceId, topicId, topicSubject, workspaceName, messages }) { ... }
```

Algorithm (single pass per message; O(N) in total events):

1. Emit the `topic` root node. Push a synthetic edge from `topic` to each spine node as it's added.
2. For each `MessageOut` in `messages` (already sorted by `created_at`) — no alternation is assumed; consecutive `user` or `agent` records are supported:
   - If `sender === 'user'`: emit a `user-message` node with parsed dispatch payload; attach as child of `topic`. Advance `sequence`.
   - If `sender === 'agent'`: emit an `agent-message` node as child of `topic`. If `message.transcript` is `null` (interrupted message), set `data.interrupted = true`, `data.hasTranscript = false`, do not emit any inner nodes or a `result-rollup`, and continue with the next message. Otherwise walk `JSON.parse(message.transcript)` events in order, maintaining:
     - `sequence` — monotonic across the whole graph.
     - `toolUseIndex: Map<toolUseId, GraphNode>` — every `tool_use` block seen so far, keyed for pairing with `tool_result`.
     - `toolResultIndex: Set<toolUseId>` — every `tool_use_id` a `tool_result` has been observed for; used to detect orphan `tool_use` blocks after the walk.
     - `subagentIndex: Map<toolUseId, GraphNode>` — every `subagent` container created so far (a `tool_use` with `name === 'Agent'` gets a paired `subagent` child).
     - `taskIndex: Map<taskId, {taskType, toolUseId}>` — every `system/task_started` seen so far; used to route later `task_progress` / `task_updated` / `task_notification` events that only carry `task_id`.
     - `parentStack` — a lookup from `parent_tool_use_id` to the enclosing `subagent` node (or the `agent-message` for `null`).
     - `pendingCompactStatus` — the most recent unresolved `system/status` compacting payload for this message, folded into the next `compact_boundary`.
   - For each event:
     - `assistant` with `thinking` block → `thinking` node under the current parent.
     - `assistant` with `text` block → `text` node under the current parent. Empty/whitespace-only `thinking` and `text` blocks emit **no node** (#249 — blank cards).
     - `assistant` with `tool_use` block → `tool-use` node; if `name === 'Agent'`, also synthesize a `subagent` child (with `invokes` edge from tool-use → subagent) and register it in `subagentIndex[toolUseId]`. If `name` matches `mcp__<server>__<tool>`, populate `data.mcpServer` / `data.mcpTool`; `toolName` is stored verbatim. Register the `tool-use` itself in `toolUseIndex[toolUseId]`.
     - `user` with `tool_result` block → `tool-result` node with `data.isError = block.is_error === true`. Attach as child of the paired `tool-use` and record `tool_use_id` in `toolResultIndex`. If the paired `tool-use` isn't found (orphan), emit under the current message with a `Diagnostic{code:'orphan_tool_result'}`. When the enclosing `tool_use` was an Agent invocation, also copy `tool_use_result.agentType|totalDurationMs|totalTokens|totalToolUseCount` onto the sibling `subagent` node's `data`.
     - `system/task_started` → `task-event` node with `subtype: 'task_started'`, `taskId`, `taskType` (`local_agent` or `local_bash`), and, for `local_agent`, `subagentType` and `prompt` **read from the event root** (not from any nested object). Attach: for `local_agent`, under the matching `subagent` (looked up by `event.tool_use_id`); for `local_bash`, under the matching `tool-use` (typically a `Bash` node — same lookup key). Register `(taskId → {taskType, toolUseId})` in `taskIndex`. If no matching `tool-use`/`subagent` — attach to the current agent-message with `Diagnostic{code:'orphan_task_event'}`.
     - `system/task_progress` | `system/task_updated` | `system/task_notification` → `task-event` node with the corresponding `subtype`. Route via `taskIndex[task_id]` to the same parent as the originating `task_started`. For `task_updated`, copy `event.patch` into `data.patch` verbatim and additionally project common patch keys (`status`, `end_time`, `is_backgrounded`) into `data.status`, `data.endTime`, `data.isBackgrounded` for renderer convenience. Missing `taskIndex` entry → attach to the current agent-message with `Diagnostic{code:'orphan_task_event'}`.
     - `system/status` → **no node emitted**. If `event.status === 'compacting'` or `event.compact_result` is set, capture the payload in `pendingCompactStatus` for the next `compact_boundary` to absorb.
     - `system/compact_boundary` → `compaction` node under the agent-message, with `trigger|preTokens|postTokens|durationMs` copied from `compact_metadata` and `compactResult` inherited from `pendingCompactStatus` if present. Clear `pendingCompactStatus`.
     - `system/init` → `system-init` node under the agent-message. Set `agent-message.data.model = event.model` — subsequent `system/init` events in the same message overwrite it (**last init wins**). Both nodes are still emitted (see IE-03 in the test plan).
     - `rate_limit_event` → **no node emitted** — telemetry noise (#249).
     - `result/success` → `result-rollup` node under the agent-message.
     - Anything unknown → skipped, `Diagnostic{code:'unknown_event_type'}` recorded.
   - Determine parent by consulting `parent_tool_use_id`: `null` → the agent-message itself (for top-level events) or the subagent whose `tool_use_id` matches. This is what allows nested subagent → subagent to work.
   - After the walk: for every `tool_use_id` in `toolUseIndex` that is not present in `toolResultIndex`, emit `Diagnostic{code:'orphan_tool_use'}`. The `tool-use` node is kept; no synthetic `tool-result` is fabricated. If `pendingCompactStatus` is still set (a `compacting` status with no matching boundary), emit a `compaction` node carrying only the status fields.
3. Return the `Graph`. Empty `summaries` / `messageSummaries`.

Determinism and testability:

- No `Date.now()` inside — `generatedAt` is passed in by the caller (defaults to caller-provided timestamp; tests can pin it).
- Given the same `messages` array, the parser always produces byte-identical output. Tested against the synthetic fixtures under `tests/fixtures/topic-graph/` — see the test plan `docs/test-plans/topic-graph-view.md` for the case list.

### 4. Layout algorithm — timeline flow

Vue Flow does not enforce a layout; it renders nodes at coordinates the app supplies. We compute coordinates in a deterministic single pass.

**Coordinate system:**

- `x = 0` is the vertical spine. Depth increases `x` rightward. Column width `COL_W = 320px`.
- `y` is time-monotonic: an `agent-message`'s `y` is at least its predecessor's `y + spacing`. Inner nodes stack downward within their subtree.
- All coordinates are integers in pixels; Vue Flow uses these as node positions.

**Algorithm** (`frontend/src/lib/graphLayout.js`, pure function):

```
function layoutGraph(graph, {rowHeight = 44, subtreeGap = 8, colWidth = 320}) -> Graph (with node.ui.x, node.ui.y set)
```

1. Traverse the graph as a tree, DFS from the `topic` root, in `sequence` order.
2. For each visited node:
   - Depth: `depth(root) = 0`, `depth(child) = depth(parent) + 1`. `x = depth * colWidth`.
   - If the node is collapsed (`ui.collapsed`), skip its subtree — inner nodes still exist in the graph object but are marked `ui.hidden = true` and given `y` inherited from the collapsed ancestor so a re-expand doesn't cause a jump.
   - `y` is a running counter incremented by `rowHeight` per visible node, plus `subtreeGap` between siblings whose subtrees closed.
3. Spine nodes (`user-message`, `agent-message`) get an extra vertical padding above them (`spineGap = 24px`) so they visually separate.
4. Edges are drawn by Vue Flow's default bezier renderer; `contains` edges use a soft grey stroke, `invokes` edges use a distinct color (accent), `summarizes` edges (future) use a dashed accent stroke.

**Collapse semantics:**

- `agent-message` and `subagent` nodes have a chevron in their card. Clicking toggles `ui.collapsed` and re-runs `layoutGraph` on the affected subtree only (or on the whole graph — see performance section).
- Default state: `agent-message.ui.collapsed = false`, `subagent.ui.collapsed = false`, `tool-use.ui.collapsed = true` for tool-uses whose result body is long (>N chars). This is a heuristic in the parser; the user can override.
- Global "Collapse all agent turns" / "Expand all" buttons in the header.

**Minimap:** yes. Vue Flow ships a `MiniMap` component that projects the current graph. Enabled by default (top-right corner). Node color in the minimap follows kind (agent-message = accent, user-message = user-color, tool-use = neutral, etc.). Toggleable via a header button, remembered in `localStorage` under `topicGraph.showMinimap`.

**Pan/zoom:** Vue Flow default controls (mouse wheel zoom, drag to pan, `Fit view` button in the corner). `onInit` fits the graph to the viewport on load.

### 5. Component structure

```
frontend/src/views/TopicGraph.vue          — page shell: fetch, toggle, error/loading states, hosts <VueFlow>
frontend/src/lib/transcriptGraph.js        — messages -> Graph (pure)
frontend/src/lib/graphLayout.js            — Graph -> Graph with x,y (pure)
frontend/src/components/graph/
  TopicRootNode.vue
  UserMessageNode.vue
  AgentMessageNode.vue
  ThinkingNode.vue
  TextNode.vue
  ToolUseNode.vue
  ToolResultNode.vue
  SubagentNode.vue
  ResultRollupNode.vue
  TaskEventNode.vue
  CompactionNode.vue         — renders pre/post token counts, trigger, duration
  SystemInitNode.vue         — visually minimal
  ParseWarningNode.vue
  NodeDetailPanel.vue        — side panel that renders the currently selected node's full payload
  SummaryOverlay.vue         — v2 stub in v1; renders SummaryArtifact[] for topic / message / node
  GraphHeaderToggle.vue      — the chat/graph segmented toggle (shared with TopicChat.vue)
```

Each node component:

- Receives a single `data` prop from Vue Flow which is our `GraphNode`.
- Renders a compact card (title line + one-line summary). Never wider than `COL_W - 24`.
- Emits `@expand-toggle` and `@select`. `TopicGraph.vue` owns the state.
- Reuses `MarkdownMessage.vue` for any markdown body (tool result content, thinking, agent text).
- Reuses the badge and color classes from `TopicChat.vue`'s `<style scoped>` — extracted into `frontend/src/components/graph/graph-node.css` and imported by each node component, so both views stay visually consistent.

### 6. Interaction spec

**Detail panel (right-side, ~380px wide):**

Selecting any node opens the detail panel. Contents per kind:

- `topic`: subject, workspace, message count, total tool_use count, aggregate cost/tokens (summed from `result-rollup` nodes). If `graph.summaries.length`, render `<SummaryOverlay scope="topic">`.
- `user-message`: full text (markdown), attachments as thumbnails/links, dispatch metadata (agent_name, adapter, subagent, model, session_id, is_new_session). "Open in chat" link → `/workspaces/:wsId/topics/:topicId#msg-<id>` in a new tab.
- `agent-message`: full final text (markdown), interrupt reason if any, model, aggregate cost/tokens for that message. When `data.interrupted` is true, the card and detail panel show an amber "interrupted" badge, the body renders `"(message interrupted)"`, and no subtree / rollup section is rendered. `messageSummaries[messageId]` rendered if present. "Open in chat" link.
- `thinking`: full thinking text, monospace, purple-tinted (matches chat view).
- `text`: full text.
- `tool-use`: tool name, full input as pretty JSON (uses `tr-json` styling), link to the paired `tool-result` node. For MCP tools the card shows two badges — server (`data.mcpServer`) and tool (`data.mcpTool`) — while the detail panel keeps the full `mcp__<server>__<tool>` name for copy-paste.
- `tool-result`: full result content, `isError` badge (red border + error icon when `data.isError` is true; identical treatment to the `error` state used by chat bubbles), link to the paired `tool-use`.
- `subagent`: `agentType`, prompt, aggregate `totalDurationMs`/`totalTokens`/`totalToolUseCount`, summary field if the pipeline populated one. Buttons "Expand subtree" / "Collapse subtree".
- `result-rollup`: cost, duration, turn count, usage table. `isError` badge.
- `task-event`: description, subtype (`task_started` / `task_progress` / `task_updated` / `task_notification`), `taskType` (`local_agent` / `local_bash`), `subagentType` for `local_agent`, `patch` block for `task_updated`, `status` / `endTime` when set.
- `compaction`: `trigger`, `preTokens` → `postTokens` (with a percentage reduction), `durationMs`, `compactResult`. Rendered as a slim horizontal divider on the agent-message subtree with a chip label so it visually reads as a boundary.
- others: raw JSON.

**Keyboard:** `Esc` clears selection and closes the panel. `→` / `←` navigate to next/previous node by `sequence`. `Space` toggles collapsed state of the selected node.

**Pan/zoom/minimap:** as per Layout section.

**Deep-link to chat message:** every `user-message` and `agent-message` card renders a small "→ chat" affordance that navigates to `/workspaces/:wsId/topics/:topicId` with a `#msg-<id>` hash. `TopicChat.vue` gets a small addition (out of scope for this document beyond noting it): on mount, if `location.hash` matches `#msg-<id>`, scroll that message into view and briefly highlight it. This can ship in the same PR as the graph view.

**Deep-link to a graph node:** the URL query parameter `?node=<nodeId>` selects that node on load. Selection updates push a `router.replace` so the URL is shareable without spamming history.

### 7. Performance considerations

Rough size envelope (from the sample): worst-case observed agent message ~130 events; a longish topic with 10 such messages is ~1300 events; larger topics might push toward 2000-3000 nodes.

Techniques applied in order until performance is good enough — measure before adding the next:

1. **Collapsed-by-default subtrees.** Every `subagent` and every `tool-use` starts collapsed. On initial render only the spine + one visible line per collapsed subtree is mounted. This alone likely handles the observed corpus.
2. **DOM node count control via Vue Flow's built-in viewport culling.** Vue Flow only renders nodes intersecting the viewport plus a small buffer. This is on by default.
3. **Lazy component mounting for detail bodies.** Node cards render title only; the full body / markdown is rendered only inside `NodeDetailPanel` when a node is selected. Cards store a plain string preview (parser-computed) — no markdown rendering per card.
4. **Coarse layout recompute.** Toggling a collapse re-runs `layoutGraph` on the whole graph (typically <5ms for 2000 nodes since it's O(N) and does no allocation beyond `y` updates). Only if measurement shows this is too slow do we scope the recompute to the affected subtree.
5. **If a topic exceeds a hard cap (default 5000 events),** the parser emits a `Diagnostic{code:'over_size'}` and truncates events after the cap on a per-message basis, keeping the spine and the first N inner events per message. Cap is configurable via a query param `?maxEvents=` for debugging.

Not applied in v1 (documented for later): virtualized column rendering, worker-based parser, IndexedDB caching of parsed graphs.

### 8. Summary-artifact extension points

Three concrete slots, all populated with `SummaryArtifact[]` (empty in v1):

- `Graph.summaries` — topic-level. Rendered above the graph as a collapsible banner ("Topic summary").
- `Graph.messageSummaries[messageId]` — attached to a specific message. Rendered inside the `agent-message` or `user-message` detail panel as a `SummaryOverlay` block, and as a small dot badge on the message node card.
- `GraphNode.summaries` — attached to any node. Rendered inside the node's detail panel.

A future backend pipeline can either:

- Return the whole `Graph` from a `GET /api/workspaces/:wsId/topics/:topicId/graph` endpoint, with `summaries` fields populated. The frontend then skips the parser and uses the response directly. `Graph.version` gates compatibility.
- Return only `SummaryArtifact[]` from a separate endpoint, and the frontend merges them into the client-parsed graph by `SummaryArtifact.scope`.

The design supports both. The IR is self-describing enough that the second (additive) mode is preferred as an incremental step, and the first (full endpoint) becomes an optimization when transcripts get large.

`SummaryOverlay.vue` in v1 is a stub that renders `SummaryArtifact.body` through `MarkdownMessage` and shows the `producedBy` / `producedAt` metadata in a footer. It's added now so the wiring is in place — nothing needs to be built later beyond producing the artifacts.

### 9. Styling and reuse

- Extract shared badge / bubble / trace styles from `TopicChat.vue` `<style scoped>` into a plain `frontend/src/styles/trace.css` and `import` it from both views. Both views end up visually consistent for tool-use badges, thinking blocks, subagent blocks, etc.
- Node cards follow the same border-radius / border-color language as chat bubbles: streaming (unused here), done (`#22c55e`), error (`#dc2626`), interrupted (`#f59e0b`).
- Colors already defined in `TopicChat.vue` are the source of truth. This design does not introduce a new palette.

### 10. File-by-file additions

New:

- `frontend/src/views/TopicGraph.vue`
- `frontend/src/lib/transcriptGraph.js`
- `frontend/src/lib/graphLayout.js`
- `frontend/src/lib/__tests__/transcriptGraph.test.js`
- `frontend/src/lib/__tests__/fixtures/sample-messages.json`
- `frontend/src/lib/__tests__/fixtures/sample-graph.json`
- `frontend/src/lib/__tests__/graphLayout.test.js`
- `frontend/src/components/graph/*.vue` (as listed above)
- `frontend/src/styles/trace.css` (extracted from `TopicChat.vue`)
- `frontend/src/components/GraphHeaderToggle.vue`

Modified:

- `frontend/src/main.js` — add `/graph` route.
- `frontend/src/views/TopicChat.vue` — mount `GraphHeaderToggle`; on mount, if `location.hash` matches `#msg-<id>`, scroll into view. Extract shared styles into `trace.css`.
- `frontend/package.json` — add `@vue-flow/core` and `@vue-flow/minimap` dependencies.

## Alternatives Considered

**Cytoscape.js.** Powerful, layout-rich, mature. Rejected because it's DOM/canvas-driven with its own rendering pipeline — node contents cannot be Vue components. Reusing `MarkdownMessage.vue`, `highlight.js`, and the existing bubble styling would require either duplicating them into Cytoscape HTML labels or maintaining a bridge. Vue Flow's model (nodes are Vue components at their own DOM position) matches how we want to render node contents.

**D3 + hand-rolled Vue integration.** Total flexibility, no library weight. Rejected because the value D3 adds is layout algorithms and force simulation — neither is needed for a deterministic timeline flow. We would end up building panning/zooming, minimap, node selection, edge routing ourselves. That's several weeks of work Vue Flow gives us on day one, and it doesn't help extensibility.

**Force-directed layout.** Aesthetic and self-organizing, but bad for a chronological conversation view — spatial position stops encoding time, and layout jitter every re-render makes it hard to return to a familiar node. Rejected on UX grounds.

**Backend graph endpoint in v1.** Would give a single canonical parse and let the server pre-aggregate. Rejected for v1 because (a) the parser is O(N) and cheap on the client, (b) we haven't validated the IR against real user needs yet — iterating on the IR shape server-side means schema migrations, client-side means editing a JS file, (c) the summary pipeline (which is the reason a backend graph makes sense) is a separate feature. Documented as the expected v2 optimization once the IR stabilizes.

**Embed graph as a panel inside `TopicChat.vue` rather than a sibling route.** Rejected — the chat view is already 950 lines. Separating the routes keeps concerns isolated, gives graph view its own URL for deep-linking, and lets us load Vue Flow only when the user actually opens the graph (route-level code splitting).

## Open Questions

- [ ] Should tool-uses spawned by subagents (nested Task) render as a `subagent-in-subagent` visual box, or flatten with a depth indicator? Design assumes the former (arbitrary nesting via `parent_tool_use_id`). Confirm during first review of a nested-Task real fixture.
- [x] `system/init` and `rate_limit_event` are usually noise. Resolved in #249: `rate_limit_event` emits no node at all; `system/init` remains a visually minimal node.
- [ ] Should the graph view respect the `verboseMode` toggle from chat view (hiding silent messages)? Design says yes — silent user-messages default hidden with a small "3 hidden" affordance to reveal. Confirm with user.
- [ ] Cost/duration aggregation on the `topic` node — sum over all `result-rollup`s, or just show the count and let the user click through? Design sums, but the aggregation may confuse (multiple subagents contribute). Revisit after seeing the numbers on real data.
- [ ] Long transcripts hitting the 5000-event truncation cap — is a hard truncation acceptable in v1, or should we page the events (e.g. "Load next 500")? Depends on how often real topics cross that line; the sample tops out at 134 events per message.

## Implementation Plan

Single-branch feature; incremental commits.

1. Add `@vue-flow/core` and `@vue-flow/minimap` to `frontend/package.json`. Sanity-render a hello-world graph on the new route.
2. Extract trace styles from `TopicChat.vue` into `frontend/src/styles/trace.css`. No behavior change — regression tests still pass.
3. Write `frontend/src/lib/transcriptGraph.js` + tests using the sample fixture. Get the IR right first; commit before any UI work.
4. Write `frontend/src/lib/graphLayout.js` + tests. Layout is pure and testable.
5. Build node components one kind at a time, starting with `user-message` and `agent-message` (the spine). Verify the sample topic renders end-to-end after each kind is added.
6. `NodeDetailPanel.vue`, keyboard navigation, deep-link handling.
7. `GraphHeaderToggle.vue` and route wiring in `main.js`. Small change in `TopicChat.vue` for the toggle and `#msg-` scroll behavior.
8. `SummaryOverlay.vue` stub — render slot is present, always empty in v1.
9. Perf pass with a large real topic. Add collapse defaults / caps as needed.
10. Doc: update `README.md` (frontend) with a short note on the graph view; add a `docs/guides/topic-graph-view.md` if the feature grows a user-facing knob worth documenting.
