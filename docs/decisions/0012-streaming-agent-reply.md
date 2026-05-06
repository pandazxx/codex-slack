---
title: "ADR-0012: Stream agent reply incrementally to the topic chat UI"
status: proposed
date: 2026-05-05
decision-makers: [architect, engineer]
consulted: [tester, sre]
informed: [doc-writer]
---

## Context and Problem Statement

Today the agent runs Claude Code with `claude --print --output-format stream-json`
inside a blocking `subprocess.run()` (`src/agent/mqtt_loop.py:_run_claude_once`,
lines 96-114). Although Claude emits one JSON event per line on stdout as it
generates, the agent buffers every line until the process exits and then
publishes a single MQTT `response` message containing the assembled
`last_response` plus the full `transcript`. The browser only sees the reply
after the agent has fully completed — typically tens of seconds, sometimes
minutes when tool calls are involved.

We want the user to see the agent "typing" in real time: text, tool calls, and
tool results as they arrive. The change should be minimal, additive, and must
not weaken the existing durable-message guarantee (the final reply is still
persisted exactly once in SQLite).

## Decision Drivers

- Perceived latency: first visible token must arrive within ~1s of the agent
  starting to respond.
- Minimal blast radius for the durable contract: do not change the existing
  `messages` table or the `/response` MQTT contract. (We do add a separate
  ephemeral `chunks` table, see persistence sub-decision below.)
- Reuse the existing transport stack (MQTT + WebSocket hub + Vue chat view) —
  no new services, no new dependencies.
- Lossy-OK on the wire for chunks: a dropped chunk in flight must not corrupt
  the conversation; the authoritative final message is still delivered.
- Survive browser refresh / WebSocket reconnect during a long agent run —
  the user must not lose the partial reply they were watching just because
  they hit F5 or their laptop slept.
- Backpressure-tolerant: slow browsers must not block the agent or the master.
- Compatible with existing transcript rendering — the frontend already knows
  how to parse `stream-json` events (`TopicChat.vue:parseTranscript`,
  lines 289-296).

## Considered Options

Two orthogonal choices. We pick one option from each set.

**Transport** (agent → master → browser):

1. **New ephemeral `chunk` MQTT topic at QoS 0; raw stream-json events
   forwarded as a new WebSocket message type; frontend renders an in-place
   live message keyed by `message_id`.** (Recommended.)
2. **Reuse the existing `/status` topic, extend its payload to carry
   stream-json events.**
3. **Switch the master↔frontend transport from WebSocket-broadcast to
   server-sent events (SSE) per topic, push chunks directly through SSE.**

**Persistence** (does the master remember chunks across browser reconnects?):

A. **No per-chunk persistence.** A reconnecting browser sees no replay; it
   waits for the final `/response`.
B. **Persist each chunk to a dedicated `chunks` table; replay on WebSocket
   connect; delete on `/response`.** (Recommended.)
C. **Append each event into the `messages.transcript` row as it arrives.**
   Mutates the durable row mid-flight.

## Decision Outcome

*Chosen options:* **Transport Option 1** (new `/chunk` topic, QoS 0, raw
stream-json passthrough) **+ Persistence Option B** (dedicated `chunks` table
with replay on WS connect and delete on `/response`).

Rationale, against the drivers:

- *Minimal blast radius for the durable contract.* The existing `/response`
  topic and `messages` table are untouched. Streaming adds a new MQTT topic,
  a new ephemeral DB table that is empty whenever no agent is mid-run, and a
  new WebSocket message type. If streaming or replay breaks, the system
  degrades to today's "single-shot reply" behaviour.
- *Lossy-OK on the wire, durable on the master.* QoS 0 keeps the hot path
  cheap; the `chunks` row is the master's local copy and survives broker
  hiccups and WebSocket drops. A browser that refreshes mid-stream gets the
  full sequence replayed from `chunks`.
- *Backpressure-tolerant.* The hub's `broadcast_threadsafe` is fire-and-forget;
  paho's QoS 0 publish is non-blocking; the `chunks` insert is a single
  prepared statement on a WAL-mode SQLite DB (microseconds at expected rates).
- *Reuses existing renderer.* The frontend already parses identical events
  from the durable transcript; the live view and the replayed view both
  append to the same array.
- *Bounded storage.* Chunks are deleted on `/response`. In steady state the
  table is near-empty; orphaned rows from agent crashes are rare and can be
  cleaned up manually. No periodic sweep is implemented in this version.

### Consequences

- *Good:* Users see incremental output (text, tool calls, tool results) within
  ~1s of the agent starting work. The durable `messages` schema is unchanged;
  recovery semantics for completed conversations are identical to today.
- *Good:* Browser refresh, laptop sleep, or a brief network blip during a
  long agent run no longer loses the partial reply. On reconnect the master
  replays every chunk seen so far for any in-progress `message_id`, and the
  live placeholder is reconstructed exactly.
- *Good:* The agent stops being a hard subprocess sink — `Popen` + line
  iteration unblocks future features such as cancellation and progress
  metrics.
- *Bad (accepted):* QoS 0 means chunk loss is still possible on the
  agent→broker→master leg; gaps will not be filled by replay (the master
  never received the dropped chunk). The final `/response` repairs the
  authoritative transcript, so the durable record is faithful.
- *Bad (accepted):* The agent process now keeps a thread blocked on
  `proc.stdout.readline()` for the duration of a Claude run. Today's
  `ThreadPoolExecutor(max_workers=4)` already permits this — no change.
- *Bad (accepted):* New ephemeral table `chunks` adds write traffic on the
  hot path (~one INSERT per stream-json line, typically 10s–100s per turn).
  SQLite under WAL handles this comfortably at our scale; we monitor.
- *Bad (accepted):* If the master crashes between a chunk being persisted and
  the corresponding `/response` arriving, orphan rows linger indefinitely.
  They are invisible to users (no in-progress `message_id` will ever resolve)
  and bounded in size. Orphaned chunks are cleaned up by
  `DELETE FROM chunks WHERE message_id = ?` when the response is saved.
  No periodic sweep is implemented in this version.

### Confirmation

- Unit test in `tests/agent/test_mqtt_loop.py` asserts that
  `_stream_claude_once` publishes one chunk per JSON line read from a
  fixture pipe and one final `response`.
- Master integration test asserts that `/chunk` MQTT messages produce both
  (a) a row in the `chunks` table with monotonically increasing `seq` and
  (b) a WebSocket frame of `type: "chunk"`. The `messages` table receives
  no row until the corresponding `/response` arrives, at which point the
  matching `chunks` rows are deleted in the same transaction.
- Master replay test: feed N `/chunk` messages, open a fresh WebSocket
  before `/response`, and assert the client receives exactly one
  `type: "chunk_replay"` frame containing the N events in order.
- Orphan cleanup test: insert chunk rows with no matching `messages` row
  and assert that `DELETE FROM chunks WHERE message_id = ?` removes them
  when the corresponding `/response` is processed. No periodic TTL sweep
  is implemented in this version; orphans are removed on response arrival.
- UAT case "live typing" in `docs/test-plans/streaming-agent-reply.md`:
  user sends a long prompt and observes incremental rendering before the
  result event arrives.
- UAT case "refresh mid-stream": user sends a long prompt, refreshes the
  browser while the agent is still streaming, and observes the partial
  reply restored within ~1s of the page reload.

## Pros and Cons of the Options

### Transport — Option 1: New `chunk` topic, QoS 0, raw passthrough (chosen)

The agent reads Claude stdout line-by-line and publishes each parsed event
to `codex-slack/workspace/{wid}/topic/{tid}/chunk` at QoS 0. Master persists
the chunk to the `chunks` table and forwards it to WebSocket clients as
`{ "type": "chunk", "message_id": ..., "event": <stream-json event> }`.
Frontend appends events to a live message keyed by `message_id`; the eventual
`/response` replaces the live message and master deletes the matching
`chunks` rows.

- Pro: Streaming and durable-final paths are independent — failure in one
  does not affect the other.
- Pro: Wire format is the same Claude `stream-json` shape the frontend
  already parses; zero translation cost.
- Pro: Lossy chunks (broker hop) are repaired by the guaranteed final
  message; reconnect gaps (master → browser) are repaired by the replay
  out of `chunks`.
- Con: Two topics carry related state — slight extra branching in the
  consumer.

### Transport — Option 2: Reuse `/status` topic for chunks

Extend status payload to carry stream-json events, e.g.
`{"state": "streaming", "event": {...}}`.

- Pro: Fewer MQTT topics; one fewer subscription on the master.
- Con: Conflates two distinct semantics (lifecycle state vs. content
  delivery). `/status` today is a tiny coarse signal; piping a high-volume
  byte stream through it muddies the contract.
- Con: Status is QoS 0 and broadcast-only; we'd still want to filter chunk
  payloads in the DB-write path, which means the routing logic gets more
  conditional, not less.

### Transport — Option 3: Server-sent events from master to frontend

Replace WebSocket broadcast with per-topic SSE; push chunks straight from
master to the SSE stream.

- Pro: Slightly simpler than WebSocket for one-way data.
- Con: We already have a working WebSocket hub used for status and final
  response; switching transports is a much larger change for no incremental
  benefit on the streaming path.
- Con: Frontend currently posts no data over the socket but architecturally
  the WebSocket leaves room for future client→server signals (e.g. cancel).
  SSE forecloses that.

### Persistence — Option A: No per-chunk persistence

A reconnecting browser sees no replay; the live placeholder is lost on
refresh and the user waits for the final `/response`.

- Pro: Zero database writes on the hot path. Simplest possible master.
- Con: Browser refresh during a long agent run silently discards the partial
  reply the user was watching. UX regression vs. expectations set by every
  other modern chat product. This was the v1 behaviour and was the most
  common complaint in early dogfooding.
- Con: Laptop sleep / brief network drops produce the same poor UX.

### Persistence — Option B: Dedicated `chunks` table (chosen)

Each chunk is INSERTed into a small `chunks` table keyed by `message_id` and
ordered by `seq`. On WebSocket connect the master finds any `message_id`
with rows in `chunks` but no matching row in `messages`, and emits a single
`type: "chunk_replay"` frame containing the ordered events. On `/response`
arrival, the matching `chunks` rows are DELETEd in the same transaction
that INSERTs into `messages`. Orphaned chunks are cleaned up by
`DELETE FROM chunks WHERE message_id = ?` when the response is saved.
No periodic sweep is implemented in this version.

- Pro: Browser refresh / reconnect during streaming restores the partial
  reply within ~1s of page load.
- Pro: Keeps the durable `messages` schema invariant intact ("one row per
  finished message") — chunks live in their own table.
- Pro: Bounded storage: empty whenever no agent is mid-run; orphan rows
  are removed on `/response` arrival in the same transaction.
- Pro: Cheap writes (single prepared INSERT, no FK, no joins) on a
  WAL-mode SQLite.
- Con: Adds one DB write per stream-json line on the hot path; the DELETE
  on `/response` is a small extra cost. Acceptable at expected volumes.
- Con: Extra branch in the master MQTT handler and a new replay branch in
  the WebSocket connect path.

### Persistence — Option C: Append into the `messages.transcript` row mid-flight

Same idea as B but mutating the durable row directly: on first chunk INSERT
a row with `streaming = 1`, on each chunk UPDATE the `transcript` column,
on `/response` finalise.

- Pro: One table instead of two; no DELETE step.
- Con: Breaks the "one row per finished message" invariant. Every consumer
  of `messages` (REST list, summarisation, exports) now must filter or
  understand `streaming = 1`. Migration cost across the codebase.
- Con: Repeated UPDATE of a TEXT column the size of the full transcript so
  far is O(N²) write amplification over the turn — much heavier than B's
  O(N) inserts.
- Con: A crashed agent leaves a half-built row visible in the UI's history
  view forever, requiring an explicit cleanup path.

## Implementation Notes (binding)

- Agent: replace `_run_claude_once` `subprocess.run` with `subprocess.Popen`
  + line iteration; publish each parsed line to the new `/chunk` topic at
  QoS 0 with payload
  `{"message_id": <reply_message_id>, "agent_name": ..., "seq": N, "event": <raw stream-json object>}`.
  After the loop exits, publish the existing `/response` message (unchanged
  shape) at QoS 1.
- Reply `message_id` is generated **before** the stream starts (today it is
  generated at line 234 of `mqtt_loop.py`), so chunks and the final response
  share the same id.
- DB schema gains a `chunks` table (see design doc); `init_db` creates it
  and the `idx_chunks_message` index.
- Master `mqtt_client._on_message`:
  - `chunk` branch: INSERT into `chunks (message_id, topic_id, seq, event, created_at)`
    then forward a `type: "chunk"` WebSocket frame.
  - `response` branch: in a single transaction, INSERT the `messages` row
    (today's behaviour) and DELETE FROM `chunks` WHERE `message_id = ?`.
- Master WebSocket connect handler (`/ws/{topic_id}` in `main.py`): replay
  happens **before** `hub.connect` to avoid a race where a live chunk
  arrives between accept and replay. Query for any `message_id` in `chunks`
  for this `topic_id` that has no matching row in `messages`; for each such
  id, send one `{"type": "chunk_replay", "message_id": ..., "events": [...]}`
  frame; then register with the hub.
- No background TTL sweep is implemented in this version. Orphaned chunks
  are cleaned up by `DELETE FROM chunks WHERE message_id = ?` when the
  response is saved. Manual cleanup query:
  `DELETE FROM chunks WHERE message_id NOT IN (SELECT id FROM messages)`.
- `ws_hub` needs no change — it is content-agnostic.
- Frontend:
  - On `type: "chunk"`: append to in-memory live message keyed by
    `message_id` (today's draft behaviour).
  - On `type: "chunk_replay"`: same code path as receiving each event in
    order (initialise the live placeholder then push each event).
  - On `type: "message"` with matching `message_id`: replace the live
    placeholder with the authoritative durable message.
