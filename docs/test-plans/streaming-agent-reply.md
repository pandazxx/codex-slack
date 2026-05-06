# Test Plan: Streaming Agent Reply

**Status:** draft
**Date:** 2026-05-05
**Design doc:** [docs/design/streaming-agent-reply.md](../design/streaming-agent-reply.md)
**Related ADR:** [ADR-0012](../decisions/0012-streaming-agent-reply.md)

## Scope

This plan covers the end-to-end streaming pipeline introduced by the
streaming-agent-reply feature:

1. Agent: `_stream_claude_once` / `_run_claude` in `src/agent/mqtt_loop.py` —
   per-line chunk publication to MQTT.
2. Master MQTT client: `_save_chunk` and amended `_save_agent_response` in
   `src/master/mqtt_client.py` — chunk persistence and atomic cleanup.
3. Master WebSocket: `_replay_in_progress_chunks` in `src/master/main.py` —
   reconnect replay.
4. Frontend rendering in `TopicChat.vue` — live activity rows, show-trace
   toggle, historical message rendering.

Out of scope: cancellation of in-flight runs, Codex streaming, cross-master
failover.

---

## Test cases

| ID | Description | Type | Pass criteria |
|----|-------------|------|---------------|
| TC-01 | Happy path — agent streams chunks; frontend shows live activity rows; final message replaces placeholder | needs-human | Browser shows incremental activity rows (tool calls, text deltas) as chunks arrive; on `/response` the placeholder is replaced by the durable message with `streaming: false`; the trace collapses to `▶ Show trace (N steps)` |
| TC-02 | Chunk persistence — chunks written to DB; deleted atomically on `/response` | automated | After N chunks arrive the `chunks` table has N rows for the `message_id`; after `/response` arrives the table has 0 rows for that `message_id` and the `messages` table has exactly 1 row |
| TC-03 | Browser refresh mid-stream — chunk_replay restores the placeholder within ~1s | needs-human | User presses F5 while the agent is running; the REST history loads completed messages; the WS connect triggers a `chunk_replay` frame; the partial reply reappears in the UI within ~1s |
| TC-04 | WS reconnect mid-stream — same as TC-03 | automated | After N chunks are persisted, a new WebSocket client connects; the first frame received is `chunk_replay` with all N events in seq order; no `chunk_replay` is sent for a completed `message_id` |
| TC-05 | Session-expiry retry — synthetic retry chunk resets activity rows; shows retry notice | automated | When `_run_claude` detects session-not-found it publishes a `{type: system, subtype: retry}` chunk before the second attempt; master deletes prior chunks for that `message_id` and inserts the retry row; seq counter is not reset between attempts |
| TC-06 | Tool-use label extraction — Bash/Read/Agent/Grep labels correct | automated | `classifyEvent` returns `tool_use` for `assistant` events with a `tool_use` content block; `toolUseLabel` returns the correct prefixed label string for Bash, Read, Agent, Grep, Write, Edit, Glob, WebFetch, and an unknown tool name |
| TC-07 | Folded events — `tool_result` and `thinking` render as collapsible `···` | automated | `classifyEvent` returns `folded` for `user` events with a `tool_result` content block and for `assistant` events with a `thinking` content block |
| TC-08 | Show-trace toggle — after completion trace is folded; click expands; transcript JSON is source of truth | needs-human | Completed message bubble shows `▶ Show trace (N steps)` collapsed; clicking expands the full activity list; the rows match the `transcript` JSON in the DB row, not the in-memory live stream state |
| TC-09 | Historical message — traceRows derived from transcript JSON; same rendering as live | automated | `transcriptToRows` called on a stored transcript JSON string produces the same classified rows that would be produced by processing the events as live chunks; `hidden` events are excluded; `result` event is excluded |
| TC-10 | Agent crash (no `/response`) — chunks remain in DB; `streaming: true` spinner stays; no collision with next `message_id` | automated | After N chunks are inserted with no matching `messages` row, the chunks remain; a second message with a different `message_id` does not touch the first set of chunks |
| TC-11 | `_save_chunk` DB failure — live broadcast still happens; failure is logged | automated | When the SQLite write in `_save_chunk` raises an exception, the function does not re-raise; the MQTT `_on_message` handler continues to call `hub.broadcast_threadsafe`; a log record at ERROR/WARNING level is emitted |
| TC-12 | First-chunk latency log — `ws.first_chunk` INFO line emitted on seq==0 | automated | When a chunk payload with `seq == 0` is processed by the master MQTT handler, a log record containing `ws.first_chunk` is emitted at INFO level |

---

## Happy path flow (reference)

```
User sends message
  -> Master publishes /prompt (QoS 1)
  -> Agent spawns Claude via Popen (line-buffered)
  -> For each stdout line: agent publishes /chunk (QoS 0)
  -> Master receives /chunk: _save_chunk inserts row; hub broadcasts type=chunk
  -> Browser handleChunk: classifies event, updates live placeholder
  -> Claude exits: agent publishes /response (QoS 1)
  -> Master receives /response: _save_agent_response INSERTs messages row AND
     DELETEs chunks row in one transaction; hub broadcasts type=message
  -> Browser finaliseMessage: replaces placeholder; derives traceRows from
     transcript JSON; sets streaming=false
```

## Edge cases and failure modes

- Chunk dropped by broker (QoS 0): gap visible until next chunk; final
  `/response` repairs the durable transcript; replay on reconnect misses the
  dropped event (accepted).
- Master restarts mid-stream: QoS-0 chunks in flight are lost; already-persisted
  chunks survive SQLite; final `/response` (QoS 1) is queued and delivered on
  reconnect, triggering atomic chunk delete.
- Two browsers on same topic: both receive identical live chunks and identical
  `chunk_replay` on connect; both apply the replace-placeholder step on
  `/response`.
- `_save_chunk` fails (disk full, DB locked): live broadcast continues; operator
  sees log error; replay will be incomplete on next reconnect (degraded mode).
- Orphaned chunks (agent crash, no `/response`): rows remain in `chunks` table;
  the spinner stays on refresh until the page is force-reloaded past the
  in-progress state; a new user message produces a fresh `message_id` with no
  collision (TC-10).

## Non-functional requirements

- First visible chunk in browser within ~1s of Claude producing its first event.
- Master `ws.first_chunk` INFO log line emitted when seq==0 chunk arrives (TC-12).
- `_save_chunk` failure must never propagate an exception to the MQTT event loop
  (TC-11).
- Chunk INSERT and matching messages DELETE are atomic (both or neither commit)
  (TC-02).
- Replay query is an O(N) range scan over `(message_id, seq)` composite index.
