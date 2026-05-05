# Design: Streaming Agent Reply

**Status:** draft
**Author:** architect
**Date:** 2026-05-05
**Related ADRs:** [ADR-0011](../decisions/0011-streaming-agent-reply.md), [ADR-0005 v3 architecture](../decisions/0005-v3-system-architecture.md)

## Problem Statement

Agent replies are delivered as a single MQTT message after the Claude
subprocess finishes. Long replies (or replies involving tool calls) can take
tens of seconds to minutes; the user sees nothing until the very end. We want
incremental rendering so the UI shows the agent "typing" — text deltas, tool
calls, and tool results — as Claude emits them.

## Goals

- First visible chunk in the browser within ~1s of Claude producing its first
  `assistant` event.
- No change to the durable `messages` schema: the existing `/response` MQTT
  path still produces exactly one row per finished agent message.
- Same wire format as today's transcript (Claude `stream-json` events), so
  the frontend transcript renderer is reused for both live and historical
  views.
- Browser refresh, WS reconnect, or laptop sleep during a long agent run
  restores the partial reply within ~1s of reconnect, by replaying chunks
  that the master has persisted to a new ephemeral `chunks` table.
- Additive change: if streaming or replay fails, the system degrades to
  today's "single-shot reply" behaviour.

## Non-Goals

- Cancellation of an in-flight Claude run. Touching `Popen` makes this
  cheaper to add later but is out of scope here.
- Streaming for the Codex adapter. Codex is request/response and not in
  current product focus; the design accommodates it but does not implement
  it.
- Backpressure-aware delivery to slow clients. Chunks are best-effort on the
  wire; the authoritative final message repairs any visual gap.
- Cross-master replay (multiple master instances sharing a chunk store). The
  product is single-master; a master crash is handled by the TTL sweep, not
  by failover.

## Proposed Design

### High-level flow

```mermaid
sequenceDiagram
    participant U as User (browser)
    participant M as Master (FastAPI)
    participant B as MQTT broker
    participant A as Agent worker
    participant C as Claude CLI

    U->>M: POST /messages (text)
    M->>B: publish /prompt (QoS 1)
    B->>A: deliver /prompt
    A->>B: publish /status {"state":"thinking"} (QoS 0)
    B->>M: deliver /status
    M->>U: ws send {type:"status", state:"thinking"}
    A->>C: spawn Popen("claude --output-format stream-json ...")
    loop one line per Claude event
        C-->>A: {"type":"assistant", ...}\n
        A->>B: publish /chunk {message_id, seq, event} (QoS 0)
        B->>M: deliver /chunk
        M->>M: INSERT INTO chunks (message_id, topic_id, seq, event, ...)
        M->>U: ws send {type:"chunk", message_id, seq, event}
        Note over U: append to live message
    end
    C-->>A: {"type":"result", ...}\n  (process exits)
    A->>B: publish /response {message_id, last_response, transcript} (QoS 1)
    B->>M: deliver /response
    M->>M: BEGIN; INSERT INTO messages (...); DELETE FROM chunks WHERE message_id=?; COMMIT
    M->>U: ws send {type:"message", message_id, ...}
    Note over U: replace live message with durable one (same id)
    A->>B: publish /status {"state":"idle"} (QoS 0)
```

### Reconnect / refresh flow

```mermaid
sequenceDiagram
    participant U as User (browser)
    participant M as Master (FastAPI)
    Note over U,M: Agent is mid-stream; chunks already persisted on master

    U->>M: GET /api/.../messages  (REST history, finished messages only)
    M-->>U: [...completed messages...]
    U->>M: WS connect /ws/{topic_id}
    M->>M: SELECT message_id FROM chunks WHERE topic_id=? AND message_id NOT IN (SELECT id FROM messages)
    loop for each in-progress message_id
        M->>M: SELECT event FROM chunks WHERE message_id=? ORDER BY seq
        M->>U: ws send {type:"chunk_replay", message_id, agent_name, events:[...]}
        Note over U: reconstruct live placeholder
    end
    Note over U,M: New chunks (from now on) arrive normally as type:"chunk"
```

### Schema change

Add an ephemeral `chunks` table to the SQLite schema in `src/master/db.py`.
Append the following to `_SCHEMA` and add `"chunks"` to `TABLES`:

```sql
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL,
    topic_id    TEXT NOT NULL,
    seq         INTEGER NOT NULL,       -- monotonically increasing per message_id
    event       TEXT NOT NULL,          -- raw stream-json event JSON
    created_at  REAL NOT NULL DEFAULT (unixepoch('now','subsec'))
);
CREATE INDEX IF NOT EXISTS idx_chunks_message ON chunks (message_id, seq);
CREATE INDEX IF NOT EXISTS idx_chunks_topic ON chunks (topic_id);
CREATE INDEX IF NOT EXISTS idx_chunks_created ON chunks (created_at);
```

Notes on the schema:

- No FK on `message_id` — the matching `messages` row is created **after**
  the last chunk arrives, so a FK would be violated for the entire stream.
- `topic_id` is denormalised onto the row so the WS-connect replay query
  can scope by topic without a join.
- `seq` is supplied by the agent (the per-message counter incremented in
  `_stream_claude_once`); the master trusts it. The composite index
  `(message_id, seq)` makes ordered replay an O(N) range scan.
- `created_at` is REAL (sub-second unix epoch) for cheap TTL comparison.
- No new migration entry needed in `_MIGRATIONS` — `init_db` runs
  `executescript(_SCHEMA)` with `IF NOT EXISTS`, which creates the table on
  fresh and existing DBs alike.

### Component changes

#### 1. Agent (`src/agent/mqtt_loop.py`)

Two new helpers; `_run_claude_once` becomes a thin wrapper around them:

```python
def _chunk_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/chunk"


def _stream_claude_once(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,        # generated by caller; chunks + response share this id
    agent_name: str,
    worktree: str,
    text: str,
    session_id: str | None,
    is_new_session: bool,
    subagent: str | None,
    model: str | None,
    system_prompt: str | None,
) -> tuple[str, str | None, str | None, bool]:
    """Run Claude streaming chunks via MQTT. Returns (output, new_sid, transcript, is_error)."""
    cmd = [...same as today...]
    chunk_topic = _chunk_topic(workspace_id, topic_id)
    events: list[dict] = []
    new_session_id: str | None = None
    output: str | None = None
    is_error = False
    seq = 0
    proc = subprocess.Popen(
        cmd, cwd=worktree,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,            # line-buffered
    )
    try:
        for line in proc.stdout:                 # blocking line iteration
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            events.append(event)
            client.publish(chunk_topic, json.dumps({
                "message_id": reply_message_id,
                "agent_name": agent_name,
                "seq": seq,
                "event": event,
            }), qos=0)
            seq += 1
            if event.get("type") == "result":
                new_session_id = event.get("session_id")
                output = event.get("result") or event.get("last_response")
                is_error = bool(event.get("is_error"))
        proc.wait()
        if not output:
            err = (proc.stderr.read() or "").strip()
            output = err or "(no output)"
        transcript = json.dumps(events) if events else None
        return output, new_session_id, transcript, is_error
    except FileNotFoundError:
        return "(claude CLI not found in agent container)", None, None, True
    except Exception as exc:
        try:
            proc.kill()
        finally:
            return f"(claude error: {exc})", None, None, True
```

`_process_prompt` (lines 160-243) needs three changes:

1. Generate `reply_message_id = str(uuid.uuid4())` **before** invoking the
   adapter (today it is generated at line 234, after the run).
2. Pass `client`, `workspace_id`, `topic_id`, `reply_message_id`,
   `agent_name` into `_run_claude` (and through to `_stream_claude_once`).
3. Use `reply_message_id` in the existing `/response` publish.

Codex adapter (`_run_codex`) is unchanged. Codex emits no streaming events;
the response arrives as one `/response` message exactly as today. If we ever
want streaming for Codex, the same `/chunk` topic is available.

Session-expiry retry path (`_run_claude`, lines 126-143) keeps working
unchanged: the second attempt streams its own chunks under the same
`reply_message_id`. The frontend will see the (truncated) first attempt's
chunks discarded when the second attempt's chunks overwrite by `seq` order.
This is acceptable cosmetic noise on a rare error path.

#### 2. Master (`src/master/mqtt_client.py`)

Add the chunk topic to the subscription list and a third branch in
`_on_message`. The chunk branch INSERTs into `chunks` and broadcasts; the
response branch is amended to DELETE the matching chunk rows in the same
transaction that writes the durable message.

```python
_CHUNK_TOPIC = "codex-slack/workspace/+/topic/+/chunk"

# in _on_connect:
client.subscribe(_CHUNK_TOPIC, qos=0)

# new helper:
def _save_chunk(db_path: str, topic_id: str, payload: dict) -> None:
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO chunks (message_id, topic_id, seq, event)"
                " VALUES (?, ?, ?, ?)",
                (payload["message_id"], topic_id, payload["seq"],
                 json.dumps(payload["event"])),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Persistence failure must not break the live broadcast path.
        LOGGER.exception("mqtt.save_chunk_error topic_id=%s", topic_id)

# in _on_message:
if msg_type == "chunk":
    db_path = userdata.get("db_path")
    if db_path:
        _save_chunk(db_path, topic_id, payload)
    message = {"type": "chunk", **payload}   # message_id, seq, event, agent_name
elif msg_type == "status":
    ...
elif msg_type == "response":
    db_path = userdata.get("db_path")
    if db_path:
        _save_agent_response(db_path, topic_id, payload)   # now also deletes chunks
    message = {"type": "message", "sender": "agent", **payload}
```

The existing `_save_agent_response` is amended to delete chunks atomically
with the message insert:

```python
# inside _save_agent_response, replacing the single execute:
with conn:                                    # implicit BEGIN/COMMIT
    conn.execute(
        "INSERT OR IGNORE INTO messages (...) VALUES (...)",
        (...),
    )
    conn.execute("DELETE FROM chunks WHERE message_id = ?", (message_id,))
    if llm_session_id and agent_name:
        conn.execute("UPDATE sessions SET llm_session_id=?, updated_at=? ...", (...))
```

#### 2b. Master WebSocket connect — replay (`src/master/main.py`)

Today's `ws_endpoint` accepts the connection and registers it with the hub.
It needs one new step: query for in-progress message_ids on this topic and
emit a `chunk_replay` frame for each.

```python
@app.websocket("/ws/{topic_id}")
async def ws_endpoint(topic_id: str, websocket: WebSocket) -> None:
    await websocket.accept()
    app.state.hub.connect(topic_id, websocket)
    try:
        await _replay_in_progress_chunks(websocket, topic_id, app.state.db_path)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        app.state.hub.disconnect(topic_id, websocket)


async def _replay_in_progress_chunks(ws: WebSocket, topic_id: str, db_path: str) -> None:
    def _query() -> list[tuple[str, list[dict]]]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            ids = [r["message_id"] for r in conn.execute(
                "SELECT DISTINCT c.message_id FROM chunks c"
                " LEFT JOIN messages m ON m.id = c.message_id"
                " WHERE c.topic_id = ? AND m.id IS NULL",
                (topic_id,),
            ).fetchall()]
            out: list[tuple[str, list[dict]]] = []
            for mid in ids:
                rows = conn.execute(
                    "SELECT event FROM chunks WHERE message_id = ? ORDER BY seq",
                    (mid,),
                ).fetchall()
                out.append((mid, [json.loads(r["event"]) for r in rows]))
            return out
        finally:
            conn.close()

    # SQLite call is sync; run in default executor to avoid blocking the loop.
    streams = await asyncio.get_running_loop().run_in_executor(None, _query)
    for message_id, events in streams:
        if not events:
            continue
        await ws.send_json({
            "type": "chunk_replay",
            "message_id": message_id,
            "events": events,
        })
```

Notes:

- The `LEFT JOIN messages` filter is what defines "in progress": chunks
  exist but no durable message row does. After `/response` arrives those
  chunks are deleted, so a finished message never replays.
- Replay is per-connection — every fresh WS connection on the topic gets
  the same replay independently. Two browsers on the same topic both see
  it; this is the desired behaviour.
- The `agent_name` is intentionally absent from the replay frame — it is
  carried in the live `chunk` payload but not stored per row. The frontend
  can derive it from any `system/init` event in `events`. (If we want
  cheaper UX, store `agent_name` on the row in v2.)

`ws_hub.ConnectionHub` requires no change — it is content-agnostic.

#### 2c. Master chunk cleanup (background task)

A periodic task deletes orphaned chunks (e.g. left by a crashed agent that
never sent `/response`).

```python
# in src/master/service.py or a new src/master/chunk_sweeper.py
async def _chunk_sweep_loop(db_path: str, ttl_seconds: int, interval: int) -> None:
    while True:
        try:
            def _sweep() -> int:
                conn = sqlite3.connect(db_path)
                try:
                    cur = conn.execute(
                        "DELETE FROM chunks"
                        " WHERE created_at < unixepoch('now','subsec') - ?",
                        (ttl_seconds,),
                    )
                    conn.commit()
                    return cur.rowcount
                finally:
                    conn.close()
            n = await asyncio.get_running_loop().run_in_executor(None, _sweep)
            if n:
                LOGGER.info("chunks.sweep_deleted count=%d", n)
        except Exception:
            LOGGER.exception("chunks.sweep_error")
        await asyncio.sleep(interval)
```

Started from the FastAPI lifespan/startup hook with
`asyncio.create_task(_chunk_sweep_loop(db_path, CHUNK_TTL_SECONDS, CHUNK_SWEEP_INTERVAL_SECONDS))`.

#### 3. Frontend (`frontend/src/views/TopicChat.vue`)

State:

```js
// keyed by message_id; values are { events: [], text: '', placeholder: true }
const liveStreams = ref({})
```

WebSocket handler (replace lines 176-189):

```js
ws.onmessage = (evt) => {
  const data = JSON.parse(evt.data)
  if (data.type === 'status') {
    agentStatus.value = data.state || ''
  } else if (data.type === 'chunk') {
    handleChunk(data)
  } else if (data.type === 'chunk_replay') {
    // Reconnect / refresh: replay every persisted chunk for an in-progress
    // agent reply. Treated identically to receiving each event live.
    const { message_id, agent_name, events } = data
    for (const event of events) {
      handleChunk({ message_id, agent_name, event })
    }
  } else if (data.type === 'message') {
    // Final authoritative message — replace any live placeholder with same id
    const existingIdx = messages.value.findIndex(m => m.id === data.message_id)
    const finalMsg = {
      id: data.message_id,
      sender: data.sender || 'agent',
      agent_name: data.agent_name || null,
      text: data.last_response || data.text || '',
      transcript: data.transcript || null,
      created_at: new Date().toISOString(),
    }
    if (existingIdx >= 0) messages.value.splice(existingIdx, 1, finalMsg)
    else messages.value.push(finalMsg)
    delete liveStreams.value[data.message_id]
    scrollToBottom()
  }
}

function handleChunk({ message_id, agent_name, event }) {
  let live = liveStreams.value[message_id]
  if (!live) {
    // Derive agent_name from system/init event if it was omitted (replay path).
    let derivedAgent = agent_name || null
    if (!derivedAgent && event.type === 'system' && event.subtype === 'init') {
      derivedAgent = event.agent_name || null
    }
    live = { events: [], text: '' }
    liveStreams.value[message_id] = live
    messages.value.push({
      id: message_id,
      sender: 'agent',
      agent_name: derivedAgent,
      text: '',
      transcript: null,                          // built lazily below
      created_at: new Date().toISOString(),
      streaming: true,                            // flag for UI cue (cursor, spinner)
    })
  }
  live.events.push(event)
  if (event.type === 'assistant' && event.message?.content) {
    for (const blk of event.message.content) {
      if (blk.type === 'text' && blk.text) live.text += blk.text
    }
  }
  // mutate the placeholder message in place
  const msg = messages.value.find(m => m.id === message_id)
  if (msg) {
    msg.text = live.text
    msg.transcript = JSON.stringify(live.events)  // re-serialise for the same renderer
  }
  scrollToBottom()
}
```

Ordering note: the REST `GET .../messages` history call (`fetch` at
`TopicChat.vue:162`) must complete and populate `messages.value` before the
WS `chunk_replay` frame is processed; otherwise the replay's `messages.push`
of the live placeholder could be wiped by a later history overwrite. The
existing flow already opens the WebSocket after the history fetch resolves,
which preserves this order. Keep it that way.

Optional CSS cue: `.message.agent.streaming .bubble::after { content: '▍'; animation: blink 1s steps(2) infinite; }` so users see a cursor while the stream is open. Add a `streaming` flag in the v-bind class.

### Wire formats

#### `/chunk` MQTT payload

```json
{
  "message_id": "9c2b…",
  "agent_name": "claude",
  "seq": 7,
  "event": {
    "type": "assistant",
    "message": { "role": "assistant", "content": [{ "type": "text", "text": "Hello" }] }
  }
}
```

#### WebSocket `chunk` frame to browser

```json
{
  "type": "chunk",
  "message_id": "9c2b…",
  "agent_name": "claude",
  "seq": 7,
  "event": { ... raw stream-json event ... }
}
```

#### WebSocket `chunk_replay` frame to browser (new)

Sent once per in-progress `message_id` immediately after a WebSocket
connect/reconnect, before any further live `chunk` frames for that id.

```json
{
  "type": "chunk_replay",
  "message_id": "9c2b…",
  "events": [
    { "type": "system", "subtype": "init", "agent_name": "claude", ... },
    { "type": "assistant", "message": { "content": [{ "type": "text", "text": "Hel" }] } },
    { "type": "assistant", "message": { "content": [{ "type": "text", "text": "lo" }] } }
  ]
}
```

#### `/response` MQTT payload (unchanged)

```json
{
  "message_id": "9c2b…",
  "agent_name": "claude",
  "reply_to": "1f0e…",
  "last_response": "Hello, world.",
  "transcript": "[ ... full event array ... ]",
  "session_id": "…"
}
```

The `message_id` in `/response` matches the `message_id` used in chunks —
this is what enables the "replace placeholder" step on the frontend.

### Failure modes and behaviour

| Failure                              | Behaviour                                                              |
|--------------------------------------|------------------------------------------------------------------------|
| Chunk dropped on agent→broker→master leg (QoS 0) | Master never sees the chunk, so it is not persisted and not broadcast. Visible gap until the next chunk arrives; final `/response` repairs the durable transcript. Replay on later reconnect also misses this chunk (master never had it). |
| Chunk dropped on master→browser leg | Frame lost in flight, but `chunks` row is persisted. On the next WS message the gap is visible until refresh; refresh triggers a `chunk_replay` containing every event including the missed one. |
| Browser disconnects mid-stream then reconnects | Live placeholder rebuilt from `chunks` via `chunk_replay`. New live chunks resume normally. Worst-case staleness = the round-trip of the WS reconnect. |
| Browser refresh (F5) mid-stream | Same as reconnect: REST history loads finished messages, WS connect triggers `chunk_replay` for the in-progress id. User sees the partial reply restored within ~1s. |
| Browser connects for the first time mid-stream | `chunk_replay` delivers everything seen so far on the master; new chunks stream normally. |
| Claude exits non-zero                 | `/chunk` may have already published partial events (now persisted in `chunks`); final `/response` carries `is_error: true` and triggers the DELETE of those chunks; frontend overwrites placeholder with the error message. |
| Agent crashes between chunks and `/response` | Persisted chunks remain in the `chunks` table for that `message_id`. The frontend keeps showing the partial reply with `streaming: true`. The chunk-sweep background task deletes the orphan rows after the TTL (default 1h). The next user message produces a fresh `message_id`; the stale placeholder will not collide. **UX gap**: the spinner stays until either a new message arrives or the page is refreshed after TTL. Track in lessons-learned. |
| Master restarts mid-stream | Master loses in-flight chunk subscription; chunks already in `chunks` table survive. New chunks published while master is down (QoS 0, no retain) are dropped by the broker. On master restart, sub-resumes; new chunks persist normally. Final `/response` (QoS 1) is queued and delivered when master reconnects, triggering the chunks DELETE. Connected browsers reconnect via WS and get a `chunk_replay` of whatever survived. |
| Two clients on the same topic | Both receive the same live chunks; both receive a `chunk_replay` on connect. Both apply the same `replace placeholder` step on `/response`. No coordination needed. |
| `_save_chunk` fails (DB locked, disk full) | Logged; live broadcast still happens, so the user still sees the chunk. Replay on a future reconnect will be missing this event — accepted, this is a degraded mode and operator-visible via the log. |

### Backpressure

- Agent → broker: paho QoS 0 publish is non-blocking and uses an internal
  in-memory queue; if it fills, paho drops. Acceptable.
- Broker → master: master's MQTT client runs in its own thread (`loop_start`)
  and `_on_message` does only a JSON parse + `broadcast_threadsafe` for the
  chunk path. No DB I/O. The hot path is microseconds.
- Master → browser: `ws.send_json` is awaited per-client inside
  `ConnectionHub.broadcast`. A slow client will slow that one client's
  broadcast loop; the hub catches exceptions and discards dead sockets. We
  judge this acceptable for the single-user, self-hosted threat model
  (ADR-0009 §threat model). If a real bottleneck emerges we add
  `asyncio.wait_for(..., 1.0)` and drop the slow client.

### Chunk cleanup

Chunks are deleted on two paths:

1. **Synchronous, on `/response`.** `_save_agent_response` runs the message
   INSERT and `DELETE FROM chunks WHERE message_id = ?` in the same
   transaction. This is the common case and keeps the table near-empty in
   steady state.
2. **Asynchronous TTL sweep.** A background asyncio task in the master
   periodically deletes any chunk older than `CHUNK_TTL_SECONDS`. This
   handles the rare case where an agent crashed without producing a
   `/response`, or where the master crashed between the chunk INSERT and
   the response DELETE on a previous run.

The sweep runs every `CHUNK_SWEEP_INTERVAL_SECONDS` (default 300 s = 5 min)
and uses a single DELETE statement with the `created_at` index. Default TTL
is 3600 s (1 hour) — long enough that a slow but legitimately running agent
is never affected, short enough that orphans do not accumulate.

Operationally, `chunks` row count is a useful diagnostic: in normal
operation it is `O(active streams × events per stream)` — typically 0–500.
Sustained growth indicates an agent crash pattern; we can add an alert
on `SELECT COUNT(*) FROM chunks WHERE created_at < unixepoch('now') - 600`
in a future runbook.

### Configuration

| Key                            | Default | Purpose                                         |
|--------------------------------|---------|-------------------------------------------------|
| `CHUNK_TTL_SECONDS`            | 3600    | Chunks older than this are swept.               |
| `CHUNK_SWEEP_INTERVAL_SECONDS` | 300     | How often the sweep task wakes up.              |

Streaming itself has no toggle — it is always on. (We could gate it behind
`STREAMING_ENABLED` if rollout risk is a concern, but the additive design
makes that unnecessary.)

### Observability

- Agent log line per stream: `agent.llm_chunk topic_id=… seq=… type=…`
  (downgraded to DEBUG to avoid log flood; INFO summary at end:
  `agent.llm_done chunks=N chars=…`).
- Master log line per chunk persisted+forwarded: DEBUG only.
- Master log line per WS replay: INFO — `ws.chunk_replay topic_id=… message_id=… events=N`.
- Master log line per sweep: INFO when rows are deleted —
  `chunks.sweep_deleted count=N`; silent otherwise.
- Existing `agent.llm_start` / `agent.llm_done` / `mqtt.message` lines are
  preserved.

## Alternatives Considered

See [ADR-0011](../decisions/0011-streaming-agent-reply.md) for the full
options table. Summary:

Transport:

- *Reuse `/status` with stream-json* — rejected: conflates lifecycle and
  content semantics, complicates routing.
- *Replace WebSocket with SSE* — rejected: transport rewrite for no
  streaming-specific gain.

Persistence:

- *No persistence (v1 plan)* — rejected after dogfooding: browser refresh
  and laptop sleep silently discard the partial reply, which is a poor UX
  for the multi-minute agent runs that are the whole point of streaming.
- *Append to `messages.transcript` mid-flight* — rejected: breaks the "one
  row per finished message" invariant, forcing every consumer of `messages`
  to filter `streaming = 1`; also O(N²) write amplification because each
  update rewrites the entire growing TEXT column.

## Open Questions

- [ ] Should we add a small "first-chunk seen" log line on the master to
      help measure agent→browser latency in practice? (owner: sre)
- [ ] Do we want a CSS cursor on streaming bubbles, or is the `thinking`
      status pill in the header enough? (owner: doc-writer / UX call)
- [ ] When the session-expiry retry path fires, the frontend will see two
      streams under the same `message_id`. The proposed re-serialise of
      `live.events` makes the second stream simply append to the first.
      Is that the desired UX, or should we reset the placeholder when
      the second `system/init` arrives? Note: the agent's retry path also
      writes a second batch of chunk rows for the same `message_id` —
      we should decide whether the agent DELETEs prior chunks before
      retrying, or whether replay shows the concatenated stream. (owner:
      tester to author UAT, engineer for the agent-side decision)
- [ ] Should we store `agent_name` on the `chunks` row so `chunk_replay`
      can carry it without forcing the frontend to derive it from the
      `system/init` event? Tradeoff: trivial extra column vs. zero work
      now. (owner: engineer at implementation time)
- [ ] What happens to chunks for an archived topic? `topics.archived_at`
      is set but the `chunks` rows are not deleted. The TTL sweep still
      catches them within an hour; do we want a cascading delete on
      topic archive for cleanliness? (owner: architect, low priority)

## Implementation Plan

Phase 1 — agent streaming (1 PR):

1. Refactor `_run_claude_once` → `_stream_claude_once` using `Popen`.
2. Generate reply `message_id` upfront in `_process_prompt`.
3. Add `_chunk_topic` and per-line publish at QoS 0.
4. Unit tests with a fake Popen yielding scripted lines.

Phase 2 — master schema + persistence + forwarding (same PR or follow-up):

1. Add `chunks` table and indexes to `_SCHEMA` in `src/master/db.py`; add
   `"chunks"` to `TABLES`.
2. Subscribe to `_CHUNK_TOPIC` in `mqtt_client._on_connect`.
3. Add `_save_chunk` helper and the `chunk` branch in `_on_message`.
4. Amend `_save_agent_response` to DELETE matching chunks in the same
   transaction as the `messages` INSERT.
5. Unit test: chunk INSERT, `/response` INSERT+DELETE atomicity.

Phase 3 — master replay on WS connect (same PR):

1. Add `_replay_in_progress_chunks` helper in `src/master/main.py`.
2. Call it from `ws_endpoint` after `hub.connect`.
3. Integration test: persist N chunks, open WS, assert `chunk_replay`
   frame contents and ordering.

Phase 4 — chunk sweeper (same PR):

1. Add `_chunk_sweep_loop` task.
2. Start it from FastAPI lifespan.
3. Read `CHUNK_TTL_SECONDS` and `CHUNK_SWEEP_INTERVAL_SECONDS` from env
   with safe defaults.
4. Unit test: insert old + fresh rows, run sweep, assert only old removed.

Phase 5 — frontend live + replay rendering:

1. Add `liveStreams` state, `handleChunk`, and the `chunk_replay`
   handler to `TopicChat.vue`.
2. Add `streaming` class hook for the cursor cue.
3. Wire the placeholder-replace step to `type: 'message'` handler.
4. Verify history-fetch-then-WS-connect ordering is preserved.

Phase 6 — UAT and docs:

1. Test plan in `docs/test-plans/streaming-agent-reply.md` (include the
   "refresh mid-stream" UAT).
2. Reference entry in `docs/references/config.md` for the new
   `CHUNK_TTL_SECONDS` / `CHUNK_SWEEP_INTERVAL_SECONDS` keys.
3. Lesson entry once shipped: cover the "agent crash leaves orphan
   chunks until TTL" caveat and the diagnostic queries from the
   Observability section.
