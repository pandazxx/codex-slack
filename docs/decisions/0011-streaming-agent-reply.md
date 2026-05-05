---
title: "ADR-0011: Stream agent reply incrementally to the topic chat UI"
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
- Minimal blast radius: do not change durable storage, the SQLite schema, or
  the existing `/response` MQTT contract.
- Reuse the existing transport stack (MQTT + WebSocket hub + Vue chat view) —
  no new services, no new dependencies.
- Lossy-OK for chunks: a dropped chunk must not corrupt the conversation; the
  authoritative final message is still delivered.
- Backpressure-tolerant: slow browsers must not block the agent or the master.
- Compatible with existing transcript rendering — the frontend already knows
  how to parse `stream-json` events (`TopicChat.vue:parseTranscript`,
  lines 289-296).

## Considered Options

1. **New ephemeral `chunk` MQTT topic at QoS 0; raw stream-json events
   forwarded as a new WebSocket message type; frontend renders an in-place
   live message keyed by `message_id`.** (Recommended.)
2. **Reuse the existing `/status` topic, extend its payload to carry
   stream-json events.**
3. **Switch the master↔frontend transport from WebSocket-broadcast to
   server-sent events (SSE) per topic, push chunks directly through SSE.**
4. **Persist every chunk to SQLite as it arrives and have the frontend tail
   the message row.**

## Decision Outcome

*Chosen option:* Option 1 — **new `chunk` topic, QoS 0, raw stream-json
passthrough**.

Rationale, against the drivers:

- *Minimal blast radius.* The existing `/response` topic and DB write path are
  untouched. The streaming path is purely additive: new MQTT topic, new
  WebSocket `type`, new frontend handler. If streaming breaks, the system
  degrades to today's behaviour.
- *Lossy-OK with hard guarantees on the tail.* QoS 0 fits the semantics:
  losing a chunk only causes a momentary visual gap that the final `response`
  message immediately repairs (same `message_id`).
- *Backpressure-tolerant.* The hub's `broadcast_threadsafe` is fire-and-forget;
  paho's QoS 0 publish is non-blocking; no per-client queue is needed.
- *Reuses existing renderer.* The frontend already parses identical events
  from the durable transcript; the live view appends to the same array.

### Consequences

- *Good:* Users see incremental output (text, tool calls, tool results) within
  ~1s of the agent starting work. Operators see no change in storage or
  recovery semantics. The change is small (~80 lines of Python, ~50 lines of
  Vue) and reverts cleanly.
- *Good:* The agent stops being a hard subprocess sink — `Popen` + line
  iteration unblocks future features such as cancellation and progress
  metrics.
- *Bad (accepted):* QoS 0 means chunk loss is possible on broker hiccup or
  WebSocket congestion; the live view may briefly skip ahead. The final
  `response` message corrects this, so the conversation history is always
  faithful.
- *Bad (accepted):* The agent process now keeps a thread blocked on
  `proc.stdout.readline()` for the duration of a Claude run. Today's
  `ThreadPoolExecutor(max_workers=4)` already permits this — no change. If we
  ever exceed 4 concurrent topics per workspace we revisit (orthogonal).
- *Bad (accepted):* A browser that connects mid-stream sees no replay; it gets
  the `thinking` status and then the final `response`, exactly as today. We
  judge replay as out of scope for v1.

### Confirmation

- Unit test in `tests/agent/test_mqtt_loop.py` asserts that
  `_stream_claude_once` publishes one chunk per JSON line read from a
  fixture pipe and one final `response`.
- Master integration test asserts that `/chunk` MQTT messages produce
  WebSocket frames of `type: "chunk"` and that DB writes only happen on
  `/response`.
- UAT case "live typing" in `docs/test-plans/streaming-agent-reply.md`:
  the user sends a long prompt and observes incremental rendering before the
  result event arrives.

## Pros and Cons of the Options

### Option 1: New `chunk` topic, QoS 0, raw passthrough

The agent reads Claude stdout line-by-line and publishes each parsed event
to `codex-slack/workspace/{wid}/topic/{tid}/chunk` at QoS 0. Master forwards
each chunk to WebSocket clients as `{ "type": "chunk", "message_id": ..., "event": <stream-json event> }`. Frontend appends events to a live message
keyed by `message_id`; the eventual `/response` replaces the live message.

- Pro: Streaming and durable paths are independent — failure in one does not
  affect the other.
- Pro: Wire format is the same Claude `stream-json` shape the frontend
  already parses; zero translation cost.
- Pro: Lossy chunks are repaired by the guaranteed final message.
- Con: Two topics carry related state — slight extra branching in the
  consumer.
- Con: Mid-stream reconnect has no replay (acceptable for v1).

### Option 2: Reuse `/status` topic for chunks

Extend status payload to carry stream-json events, e.g.
`{"state": "streaming", "event": {...}}`.

- Pro: Fewer MQTT topics; one fewer subscription on the master.
- Con: Conflates two distinct semantics (lifecycle state vs. content
  delivery). `/status` today is a tiny coarse signal; piping a high-volume
  byte stream through it muddies the contract.
- Con: Status is QoS 0 and broadcast-only; we'd still want to filter chunk
  payloads in the DB-write path, which means the routing logic gets more
  conditional, not less.

### Option 3: Server-sent events from master to frontend

Replace WebSocket broadcast with per-topic SSE; push chunks straight from
master to the SSE stream.

- Pro: Slightly simpler than WebSocket for one-way data.
- Con: We already have a working WebSocket hub used for status and final
  response; switching transports is a much larger change for no incremental
  benefit on the streaming path.
- Con: Frontend currently posts no data over the socket but architecturally
  the WebSocket leaves room for future client→server signals (e.g. cancel).
  SSE forecloses that.

### Option 4: Persist every chunk to SQLite

Append each event to the message row as it arrives; the frontend polls or
tails the row.

- Pro: Free replay on reconnect.
- Con: Vastly more database writes (potentially hundreds per turn) on the
  hot path; SQLite under the WAL is fine for this rate but we gain nothing
  the live-view doesn't already give us, and we still need a push channel
  to avoid polling.
- Con: Complicates the schema invariant ("one row per finished message").

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
- Master `mqtt_client._on_message` adds a third branch for `chunk` that
  forwards a WebSocket frame and performs **no** DB write.
- `ws_hub` needs no change — it is content-agnostic.
- Frontend appends an in-memory message on first chunk, mutates its
  `transcript` event array on each subsequent chunk, and is replaced by the
  authoritative `message` event when it arrives (same `message_id`).
