---
title: "ADR-0014: Vetoable topic_archiving event (pre-commit interceptor)"
status: accepted
date: 2026-05-09
decision-makers: [architect, engineer]
consulted: [tester, sre]
informed: [doc-writer, users]
---

## Context and Problem Statement

ADR-0013 ships `topic_archived` as an observe-only **post-commit** event: the topic is archived
first, the event fires afterwards, and the resulting agent reply lands in the archived view.

Operators have asked for the opposite: a **pre-commit** event that runs a staff *before* the
archive transition completes and can **veto** it (example: "don't archive — there are unanswered
review comments"). This is the `topic_archiving` event, where the `-ing` suffix is reserved in
v1 for pre-commit interceptors.

This ADR describes v2 of event-based staff actions, scoped to the archive use-case, with the
interceptor pattern designed for generalisation to other pre-commit events in future.

See GitHub issue [#156](https://github.com/pandazxx/codex-slack/issues/156).

## Decision Drivers

- **Pre-commit semantics.** The event must fire before `archived_at` is committed; the archive
  must be contingent on every matching action's verdict.
- **Synchronous HTTP response.** The caller of `DELETE /…/topics/{tid}` needs a definitive answer
  (archived, vetoed, or timeout) in the same HTTP response. No polling.
- **Structured verdict protocol.** Free-form text replies are insufficient; a machine-readable
  `{verdict, reason}` contract is required between master and agent.
- **Agent adapter transparency.** The agent is prompted with natural language and emits JSON; no
  new CLI flags needed. The adapter layer in the agent container extracts the verdict from the
  LLM output.
- **Additive, not breaking.** Existing `topic_archived` (post-commit) and all v1 event_actions
  mechanics remain unchanged. `topic_archiving` is a new event_type alongside them.
- **Operator escape hatch.** A veto should never permanently block an archive. An `override`
  query parameter on the archive endpoint lets operators bypass interceptors when needed.
- **Generalisation path.** The structured-verdict protocol and synchronous-await dispatch path
  introduced here are the building blocks for future pre-commit interceptors
  (`topic_message_sending`, `topic_creating`, etc.) — design once, not ad hoc.

## Considered Options

### Synchronous-await dispatch path

A. **asyncio.Future per prompt message_id, resolved by MQTT verdict handler.** `veto_dispatch()`
   dispatches staff (publishes MQTT prompt), registers a `Future` in `app_state.veto_futures` keyed
   by the prompt's `message_id`, then awaits all futures with a hard timeout.
B. **Polling a new DB column.** `veto_dispatch()` writes a pending row; the MQTT handler updates
   it; the endpoint polls via `asyncio.sleep` loop.
C. **In-process synchronous subprocess.** Run the agent LLM inline in a thread and await the
   thread result. Breaks session sharing and the MQTT contract.

### Structured verdict protocol

P. **New MQTT `/verdict` subtopic.** Agent publishes `{reply_to, verdict, reason}` there when
   `response_mode="verdict"` is set in the prompt payload. Master subscribes to `/verdict` and
   routes by `reply_to` to the waiting Future. The normal `/response` message also fires so the
   agent's text reasoning lands in the topic chat.
Q. **Parse verdict from `/response` payload.** No new subtopic; master tries to JSON-parse the
   response text for a verdict object.
R. **New `verdict` field in the existing `/response` payload.** Structurally extends the current
   protocol without a new subtopic.

### Timeout fallback policy

T1. **504 Gateway Timeout to caller; override available.** If the veto staff does not respond
    within N seconds, the API returns 504. The frontend offers "Override and archive anyway".
T2. **Auto-allow (silently archive).** Timeout is treated as implicit allow.
T3. **Auto-deny (require manual override).** Timeout blocks the archive until an explicit
    `?override=true` request.

### Multiple-action semantics

M1. **First-deny-wins.** Any action that denies blocks the archive. All actions that allow must
    complete (or time out) before allow is confirmed.
M2. **Majority/quorum.** More complex; no operator demand.
M3. **Last-action-wins.** Non-deterministic with parallel dispatch.

### Override mechanism

O1. **Query param `?override=true`.** Skips veto checks; proceeds directly to archive.
O2. **Separate admin endpoint.** Adds surface area; no benefit for v1.

## Decision Outcome

**Chosen: A + P + T1 + M1 + O1.** Concretely:

### 1. New event type: `topic_archiving`

A new `event_type='topic_archiving'` is added to `event_actions`. Constraints:

- `timing` must be `NULL` (timing is implicit: always pre-commit).
- `cron_expr` must be `NULL` (not a scheduler event).
- Validation mirrors `topic_archived` except the semantics are pre-commit.

The DB schema CHECK constraint is updated via a table-recreation migration (SQLite cannot modify
CHECK constraints in-place).

### 2. New `last_run_status` values

`vetoed` and `veto_timeout` are added to the `last_run_status` CHECK so operators can see whether
a veto action denied or timed out in the event-actions panel. The same migration that rebuilds
the `event_actions` table adds these values.

### 3. Synchronous-await dispatch via asyncio.Future (option A)

`veto_dispatch()` in `event_dispatcher.py`:

1. Queries `event_actions` for all enabled `topic_archiving` rows for the topic.
2. For each, resolves the staff and renders the template (same path as `_dispatch_one`).
3. Calls `dispatch_to_staff(…, response_mode="verdict")` to publish the MQTT prompt and get
   back a `message_id`.
4. Registers `app_state.veto_futures[message_id] = asyncio.Future()`.
5. `asyncio.wait(futures, timeout=VETO_TIMEOUT_S)` — default **30 s**.
6. Returns a `VetoResult(allowed, reason, timed_out)` named tuple.

`veto_futures` is a `dict[str, asyncio.Future]` initialised on `app_state` in the FastAPI
lifespan. Entries are removed after `asyncio.wait` completes (or times out), whether or not the
Future was resolved.

### 4. MQTT `/verdict` subtopic (option P)

The agent container's `mqtt_loop.py` is extended:

- When `response_mode="verdict"` is present in the prompt payload, after the LLM call completes,
  `_extract_verdict(response_text)` parses the last JSON object in the output for
  `{verdict: "allow"|"deny", reason: "..."}`. Falls back to `{"verdict": "allow", "reason": "(no verdict found)"}` if
  the LLM output contains no parseable JSON verdict.
- The agent publishes on `codex-slack/workspace/{wid}/topic/{tid}/verdict` with
  `{reply_to: message_id, verdict, reason, agent_name}` (QoS 1).
- The agent **also** publishes on `/response` as normal, so the reasoning text lands in the
  topic chat for the operator to read.

The master subscribes to `codex-slack/workspace/+/topic/+/verdict` (QoS 1). In `_on_message`,
when `msg_type == "verdict"`, it calls `loop.call_soon_threadsafe` to look up
`app_state.veto_futures[reply_to]` and set its result if still pending.

### 5. Archive endpoint contract (option T1)

`DELETE /api/workspaces/{wid}/topics/{tid}` becomes `async def` and gains
`override: bool = False` (query param).

| Scenario | HTTP status | Body |
|---|---|---|
| No `topic_archiving` actions, or all allowed | 204 | (empty) |
| At least one action denied | 423 Locked | `{"reason": "..."}` |
| Veto staff did not respond within 30 s | 504 Gateway Timeout | `{"reason": "veto staff did not respond in time"}` |
| `?override=true` | 204 | (empty — veto skipped) |

On 423 or 504, the frontend renders the reason and offers an "Override and archive anyway" button
that re-issues `DELETE` with `?override=true`.

`topic_archived` (post-commit) still fires on all successful archives (override or normal).

### 6. Multiple-action semantics (option M1)

If multiple `topic_archiving` actions exist, they all dispatch in parallel (same
`asyncio.gather` pattern as `_handle_event`). First deny wins: as soon as one Future resolves
with `verdict=deny`, `veto_dispatch` can short-circuit. In practice `asyncio.wait` collects all
completed futures and we scan them; we do not cancel the remaining pending futures early
(they run to completion or timeout for observability). The `vetoed` status is recorded for the
denying action; `ok` for the allowing ones.

### 7. `response_mode` in dispatch payload (option P)

`dispatch_to_staff()` gains an optional `response_mode: str | None = None` parameter. When
non-None, the value is included in the MQTT prompt payload as `"response_mode"`. Existing callers
pass nothing and are unaffected. Event-worker (`_dispatch_one`) does not pass `response_mode`,
preserving the fire-and-forget semantics of `topic_archived` and other post-commit events.

### Alignment with ADR-0013

- The existing queue + worker remains unchanged. `topic_archiving` does **not** go through the
  queue; it uses its own synchronous-await path in `veto_dispatch`. This is the minimum change:
  pre-commit interceptors need a return value; the queue is fire-and-forget by design.
- Loop prevention: `veto_dispatch` calls `dispatch_to_staff(sender="event")`, same as the
  existing event worker. Agent replies to veto prompts carry `sender="agent"` and fire
  `topic_message_received` normally, but the verdict machinery is separate.
- `topic_archived` still fires post-commit on successful archives (including overrides), giving
  the post-commit summariser use-case an unchanged trigger.

### Generalisation path

The `_extract_verdict` / `/verdict` protocol is generic — the same mechanism would serve
`topic_message_sending` or `topic_creating` interceptors. The only archive-specific pieces are
the `veto_dispatch` call site in `topics.py` and the event_type value. Future pre-commit events
add a new `event_type` and a new `veto_dispatch` call at their emit site; no protocol changes
needed.

### Consequences

- **Good**
  - Operators get a genuine pre-commit veto: archive is only committed after every enabled
    interceptor says allow.
  - 30 s timeout + 504 response + override button gives operators agency when agents are slow
    or misconfigured — no topic can be permanently un-archivable.
  - The `/verdict` subtopic is additive — agent containers that don't know about `response_mode`
    just publish `/response` as before; no veto fires and `veto_futures` entries time out
    (defaulting to 504, resolved by `?override=true`).
  - `last_run_status` records `vetoed` or `veto_timeout` per action, surfaced in the UI event
    panel alongside existing `ok`/`staff_missing`/`render_error`/`dispatch_error` statuses.
  - The prompt template can include any instructions the operator wants; the agent reasons freely
    and the adapter extracts the verdict JSON from the last JSON object in the output — no new
    CLI flags needed.
- **Bad / accepted tradeoffs**
  - `DELETE /…/topics/{tid}` is now potentially slow (up to 30 s). Clients must not assume the
    endpoint is fast. The frontend shows a loading state.
  - If no `topic_archiving` actions exist, the endpoint is unchanged in latency (same as before).
  - Veto JSON extraction relies on the agent following the prompt instructions. A poorly written
    template may produce no JSON verdict; the agent defaults to `allow`, which is permissive but
    safe (archive proceeds). Operators should test their templates.
  - A single slow or hung agent can block an archive for 30 s. The timeout bounds this; the
    override bypass removes the ceiling entirely.
  - The verdict Future dict (`app_state.veto_futures`) is in-memory: a master restart while a
    veto is in-flight will drop the Future, and the HTTP request will receive a 504 (the client
    can then retry with `?override=true`). Acceptable for v1.
  - Parallel multi-action veto with first-deny-wins does not cancel already-dispatched sibling
    agents. Agents that are still running will eventually publish a verdict that is ignored
    (Future is gone). This is benign — they may leave a chat message in the topic.

### Confirmation

- Unit tests in `tests/master/test_topic_archiving_veto.py`:
  - Allow path: `veto_dispatch` resolves all Futures with `allow` → `delete_topic` returns 204.
  - Deny path: at least one Future resolves with `deny` + reason → 423 with body.
  - Timeout path: Futures not resolved within VETO_TIMEOUT_S → 504.
  - Override path: `?override=true` skips `veto_dispatch` entirely → 204.
  - No actions path: no `topic_archiving` rows → `veto_dispatch` returns immediately → 204.
  - `topic_archived` still fires after a successful (allowed or override) archive.
  - `last_run_status` recorded correctly for allow, deny, timeout, staff-missing cases.
- Agent tests (`tests/agent/test_mqtt_loop.py`):
  - `_extract_verdict` extracts `{verdict, reason}` from inline JSON, trailing JSON, and falls
    back to `allow` when no JSON found.
  - `_process_prompt` with `response_mode="verdict"` publishes on `/verdict` in addition to
    `/response`.
- Integration test (dev env):
  - Configure a `topic_archiving` action invoking `@reviewer`; attempt archive; observe 423 when
    the staff's template asks it to deny; observe 204 after `?override=true`. `automated`.
  - Configure a `topic_archiving` action against a stopped agent; observe 504 after 30 s
    (adjusted to a shorter timeout in test config). `automated`.

## Pros and Cons of the Options

### Synchronous-await dispatch path

| Option | Pro | Con |
|---|---|---|
| A — asyncio.Future + veto_futures dict (chosen) | Zero new I/O; reuses existing async event loop; Futures can be resolved from the MQTT thread via call_soon_threadsafe | In-memory: crash drops pending futures (resolved by 504 + override) |
| B — DB polling | Durable across restarts | ~1 s polling latency per action; more complex; extra column/table |
| C — inline subprocess | Simple to reason about | Breaks session sharing; bypasses MQTT contract; not compatible with remote agent containers |

### Structured verdict protocol

| Option | Pro | Con |
|---|---|---|
| P — new /verdict subtopic (chosen) | Clean separation; /response still lands in chat; no ambiguity between text and verdict | New MQTT subscription on master and new publish path on agent |
| Q — parse /response payload | No new subtopic | Ambiguous for text that happens to contain JSON; /response already saved as a chat message before verdict is acted on |
| R — verdict field in /response payload | No new subtopic; one publish | Couples the agent-response-saving path to the veto logic; harder to test independently |

### Timeout fallback policy

| Option | Pro | Con |
|---|---|---|
| T1 — 504 + override (chosen) | Operator sees the timeout; override gives agency; no silent data loss | Slower UX if agents are systematically slow |
| T2 — auto-allow | No UX disruption for slow agents | Silently archives even when the veto staff is unhealthy; undermines the veto contract |
| T3 — auto-deny | Safest | Permanently blocks archive if agent is down; operator has no path forward without admin intervention |
