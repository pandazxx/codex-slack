# Design: Event-based staff actions

**Status:** draft
**Author:** architect
**Date:** 2026-05-07
**Related ADRs:** [ADR-0013](../decisions/0013-event-based-staff-action.md);
builds on ADR-0009 (Staff system) and ADR-0011 (Outbound notifications).
**Issue:** [#143](https://github.com/pandazxx/codex-slack/issues/143)

## Context

The Staff system (ADR-0009) gives users named LLM invocation profiles
addressable by `@mention`. Today the only way to fire a Staff is for a human
to type in the topic chat. We want to bind in-system events — a user message
arrives, an agent reply lands, a topic is archived, a cron tick — to the same
dispatch path, so a Staff can run automatically.

The mechanism must reuse the existing dispatch logic in `messages.py`
(`send_message`) end-to-end, including session sharing through
`_staff_session_key()`, MQTT topic conventions, and `staff_sessions`
bookkeeping. Event-triggered runs are mechanically identical to user-triggered
ones — only the trigger and the `sender` column on the `messages` row differ.

## Goals

- One declarative table (`event_actions`) for all four event types in scope.
- Topic-scope events: `topic_message_sent` (before/after), `topic_message_received` (after), `topic_scheduler`, `topic_archive` (after).
- Variable substitution in prompt templates with safe handling of unknown placeholders.
- Multiple actions on the same event fire independently (failure isolation).
- Event-triggered messages share sessions with user-triggered ones per `staff.session_scope`.
- Full CRUD HTTP API and Vue UI under topic settings, including enable/disable.
- Loop-safe by construction: events cannot infinitely retrigger themselves.

## Non-Goals

- **Workspace-scope events.** Schema admits `scope_type='workspace'` for
  forward compatibility, but only `'topic'` is implemented in v1. Workspace
  scope needs an output-channel decision out of scope here.
- **Sub-minute scheduler precision.** Minute-level resolution only.
- **Pipeline / message-modification hooks.** Events are observe-only; they do
  not mutate the original user/agent message. A `before` hook fires before
  the original message is published to MQTT but cannot edit or veto it.
- **Global-scope events.** Schema does not admit `'global'`; that is a future
  ADR if demand emerges.
- **Chained events** (a Staff action causing another action to fire). The
  loop-prevention rule blocks this on purpose. If chaining is needed later,
  add it via a separate "depth counter" mechanism.

## Design

### 1. Data model

New table `event_actions` in `src/master/db.py` — pure additive migration
(append a `CREATE TABLE IF NOT EXISTS` to `_SCHEMA`; no `ALTER` step).

```sql
CREATE TABLE IF NOT EXISTS event_actions (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'topic_message_sent',
                        'topic_message_received',
                        'topic_scheduler',
                        'topic_archive'
                    )),
    scope_type      TEXT NOT NULL CHECK (scope_type IN ('topic')),
    scope_id        TEXT NOT NULL,                 -- topic_id
    staff_name      TEXT NOT NULL,                 -- references staffs.name (cascade-resolved at fire time)
    prompt_template TEXT NOT NULL,                 -- {variable} placeholders, str.format_map syntax
    timing          TEXT CHECK (timing IN ('before', 'after')),
    cron_expr       TEXT,
    last_fired_at   TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    CHECK (
        (event_type = 'topic_scheduler'      AND cron_expr IS NOT NULL AND timing IS NULL)
        OR
        (event_type = 'topic_message_sent'   AND cron_expr IS NULL     AND timing IN ('before','after'))
        OR
        (event_type IN ('topic_message_received','topic_archive')
                                              AND cron_expr IS NULL     AND (timing IS NULL OR timing = 'after'))
    )
);
CREATE INDEX IF NOT EXISTS idx_event_actions_scope_event
    ON event_actions (scope_type, scope_id, event_type, enabled);
CREATE INDEX IF NOT EXISTS idx_event_actions_scheduler
    ON event_actions (event_type, enabled) WHERE event_type = 'topic_scheduler';
```

Notes:
- `id` is a UUIDv4 string (consistent with other tables in this codebase).
- The CHECK on `scope_type` lists only `'topic'` for v1. Adding `'workspace'`
  later is a `DROP TABLE … CREATE TABLE …` rebuild (SQLite cannot ALTER a
  CHECK), but since the rest of the schema is forward-compatible the rebuild
  is local.
- `last_fired_at` is updated only on successful dispatch (MQTT publish
  returned without exception). Failed dispatches do not advance the watermark
  — see "Scheduler" below.
- `enabled=0` rows are kept entirely for audit and operator UX; resolution
  filters them out.

### 2. Variable substitution

```python
from collections import defaultdict

def render_template(template: str, variables: dict[str, str]) -> str:
    # defaultdict returning {name} keeps unknown placeholders literal
    # so a misconfigured template logs a warning but does not crash.
    return template.format_map(_SafeDict(variables))

class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        LOGGER.warning("event_action.unknown_variable key=%s", key)
        return "{" + key + "}"
```

Variables exposed per event type:

| event_type | variables |
|---|---|
| `topic_message_sent` | `msgbody`, `topic_name` |
| `topic_message_received` | `msgbody`, `topic_name` |
| `topic_scheduler` | `topic_name`, `workspace_name` |
| `topic_archive` | `topic_name` |

`msgbody` is the raw `text` of the triggering message (user input for `*_sent`,
`last_response` for `*_received`). Future variables can be added without
schema changes; the dict is built per-event-type at the emit site.

Literal `{` characters in templates can be escaped as `{{` per Python's
standard `format_map` rules. This is documented in the UI help text next to
the template input.

### 3. Dispatch core extraction

Today `messages.py:send_message` does six things:

1. Validate workspace + topic.
2. Parse `@mention`, resolve Staff (or default).
3. Resolve session UUID via `_staff_session_key` + `_get_staff_session`.
4. Insert a row into `messages`.
5. Build the JSON dispatch payload, write it to the row's `transcript`,
   broadcast on the WebSocket hub.
6. Auto-start the agent container, publish to MQTT.

We extract steps 3–6 into a shared helper:

```python
# src/master/dispatch.py (new module, or near send_message in messages.py)

async def dispatch_to_staff(
    *,
    app_state,          # request.app.state — exposes db_path, hub, mqtt, settings, attachment_store
    workspace_id: str,
    topic_id: str,
    staff: sqlite3.Row, # already resolved
    prompt_text: str,
    sender: str,        # 'user' | 'event'
    raw_text: str | None = None,  # text written into messages.text; defaults to prompt_text
    attachments: list[dict] | None = None,  # optional; events do not attach files in v1
) -> str:
    """Insert message row, build payload, broadcast, MQTT-publish.
    Returns the new message_id. Mirrors what send_message did inline.
    """
```

`send_message` becomes:

1. Validate workspace + topic.
2. Parse `@mention`, resolve Staff.
3. Handle file uploads (writes to `attachments` table — stays in
   `send_message` because it is request-bound).
4. **Emit `topic_message_sent` (before)** — `emit_event(...)` returns
   immediately; handling happens in the worker.
5. Call `dispatch_to_staff(..., sender='user', raw_text=text, attachments=...)`.
6. **Emit `topic_message_sent` (after)** — same as step 4.

The event-handling side (see §4) is a single async worker that calls
`dispatch_to_staff` with `sender='event'` for each matching action. Session
sharing falls out: both paths feed the same `staff` row into
`_staff_session_key()` and `_get_staff_session()`, so an event-triggered call
and a user-triggered call land on the same `staff_sessions` row and the same
`--resume <uuid>` flag. The user-message dispatch in step 5 happens directly
on the request thread (unchanged today behaviour); event-triggered dispatches
go through the queue + worker.

### 4. Event emission points

The four emit sites push events onto a shared `asyncio.Queue` via a single
`emit_event(...)` call. A single async worker task drains the queue and does
the lookup → resolve → render → dispatch sequence per event.

```mermaid
sequenceDiagram
    participant U as User
    participant API as POST /messages
    participant MC as mqtt_client._on_message
    participant T as DELETE /topics/{id}
    participant BG as 60s background loop
    participant EM as emit_event
    participant Q as asyncio.Queue
    participant W as event_worker
    participant D as dispatch_to_staff
    participant MQTT

    U->>API: text="..."
    API->>EM: emit topic_message_sent (before)
    EM->>Q: put_nowait(event)
    API->>D: user dispatch (sender=user)
    D->>MQTT: publish prompt
    API->>EM: emit topic_message_sent (after)
    EM->>Q: put_nowait(event)

    MC->>EM: emit topic_message_received (call_soon_threadsafe)
    EM->>Q: put_nowait(event)

    T->>EM: emit topic_archive (after)
    EM->>Q: put_nowait(event)

    BG->>BG: scan due scheduler actions
    BG->>EM: emit topic_scheduler (call_soon_threadsafe)
    EM->>Q: put_nowait(event)

    loop one event at a time
        Q-->>W: await get()
        W->>W: select matching actions, resolve staff, render template
        W->>D: dispatch_to_staff (sender=event)
        D->>MQTT: publish prompt
    end
```

Concrete emit sites:

| Hook | File | Insertion point |
|---|---|---|
| `topic_message_sent` (before) | `messages.py:send_message` | After staff resolution, **before** the user's `dispatch_to_staff` call |
| `topic_message_sent` (after)  | `messages.py:send_message` | After the user's `dispatch_to_staff` returns |
| `topic_message_received`      | `mqtt_client.py:_on_message` (`msg_type == "response"` branch) | After `_save_agent_response` and `_record_agent_response`, alongside the existing `notify.notify_reply` call |
| `topic_archive`               | `topics.py:delete_topic` | After the `UPDATE topics SET archived_at = ?` commit |
| `topic_scheduler`             | `main.py:_background_tasks` | New per-minute pass over scheduler actions, see §6 |

#### `emit_event` — single threadsafe entry point

```python
def emit_event(
    *,
    app_state,
    event_type: str,
    topic_id: str,
    workspace_id: str,
    timing: str | None = None,           # 'before'|'after'|None
    variables: dict[str, str],
    # Scheduler-only: identifies the cron slot this event represents.
    # Carried through purely so the worker can log it; the watermark itself
    # has already been advanced by the scheduler tick (see §6).
    scheduler_slot: datetime | None = None,
    scheduler_action_id: str | None = None,
) -> None:
    """Push an event onto the global event queue. Safe to call from any
    thread (FastAPI handler, MQTT thread, scheduler thread). Returns
    immediately; handling happens later in event_worker."""
    event = {
        'event_type': event_type,
        'topic_id': topic_id,
        'workspace_id': workspace_id,
        'timing': timing,
        'variables': variables,
        'scheduler_slot': scheduler_slot,
        'scheduler_action_id': scheduler_action_id,
    }
    queue = app_state.event_queue
    loop = app_state.event_loop  # captured at lifespan startup
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        queue.put_nowait(event)
    else:
        loop.call_soon_threadsafe(queue.put_nowait, event)
```

`app_state.event_loop` is the asyncio loop captured at FastAPI lifespan
startup; `app_state.event_queue` is the `asyncio.Queue()` created at the
same point. Both are stable for the process lifetime.

Queue is unbounded (`asyncio.Queue()` with default `maxsize=0`). Acceptable
because emission is human-driven (max ~1/s) and events are tiny dicts. If
abuse appears, bound it later — that's a one-line change with no caller
impact.

#### `event_worker` — single consumer

```python
async def event_worker(app_state) -> None:
    queue: asyncio.Queue = app_state.event_queue
    while True:
        event = await queue.get()
        try:
            await _handle_event(app_state, event)
        except Exception:
            LOGGER.exception(
                "event_worker.handle_failed type=%s",
                event.get('event_type'),
            )
        finally:
            queue.task_done()


async def _handle_event(app_state, event: dict) -> None:
    conn = get_connection(app_state.db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='topic'"
            "   AND scope_id=?"
            "   AND event_type=?"
            "   AND enabled=1"
            "   AND (timing IS NULL OR timing=?)",
            (event['topic_id'], event['event_type'], event['timing']),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        try:
            staff = resolve_staff(
                conn, row['staff_name'],
                event['workspace_id'], event['topic_id'],
            )
            if staff is None:
                LOGGER.warning("event_action.staff_missing id=%s", row['id'])
                continue
            prompt = render_template(row['prompt_template'], event['variables'])
            await dispatch_to_staff(
                app_state=app_state,
                workspace_id=event['workspace_id'],
                topic_id=event['topic_id'],
                staff=staff,
                prompt_text=prompt,
                sender='event',
                raw_text=prompt,
            )
        except Exception:
            LOGGER.exception("event_action.dispatch_failed id=%s", row['id'])
```

Worker properties:

- **Concurrency is exactly 1.** One worker task, one event at a time. This
  removes races between MQTT-thread, request-thread, and scheduler-thread
  emissions firing against the same `event_actions` row without needing
  explicit locking. The `dispatch_to_staff` call inside `_handle_event` is
  awaited, so the next event waits behind it.
- **Per-event error isolation.** Each iteration of the worker loop is wrapped
  in `try/except`. One bad event logs `event_worker.handle_failed` and the
  worker continues. Inside `_handle_event`, each per-action iteration is
  *also* wrapped — one bad action does not skip its siblings.
- **Lifecycle.** Started from FastAPI's lifespan startup
  (`asyncio.create_task(event_worker(app.state))`), cancelled on shutdown.
  Pending events still in the queue at shutdown are lost — accepted, see
  ADR-0013 Consequences.
- **No backpressure on emitters.** The queue is unbounded; `emit_event`
  always returns immediately. Emitter call sites never block.

### 5. Loop prevention

The `messages.sender` column gets a third valid value: `'event'`. Hook gates:

| Hook | Fires only when |
|---|---|
| `topic_message_sent` (before/after) | the triggering message has `sender='user'` |
| `topic_message_received` | the agent message has `sender='agent'` (the only sender produced by `_save_agent_response`) |
| `topic_archive` | always (no message involved) |
| `topic_scheduler` | always (no message involved) |

Because `dispatch_to_staff(..., sender='event')` writes
`sender='event'` into `messages`, no event-triggered insertion can match the
`sender='user'` predicate that gates `topic_message_sent`. Agent replies are
produced exclusively by `_save_agent_response` with `sender='agent'`,
independently of who initiated the dispatch — so an event-triggered run that
yields an agent reply *will* fire `topic_message_received` exactly like a
user-triggered run does. This is the desired behaviour: a scheduled
`@summariser` posting a summary should be eligible to trigger a downstream
`@translator` if one is configured. It is **not** an infinite loop because
`@translator`'s reply will fire `topic_message_received` only if a third
action subscribes to it, and so on — chains terminate because each step
requires explicit operator configuration. (If we later want to harden this,
ADR-0013 alternative Z — a depth counter — is the lift.)

### 6. Scheduler

Hooked into the existing 60 s loop in `main.py:_background_tasks` (currently
~lines 65–150). New pass at the top of each tick, before per-workspace work.
The tick is responsible for *deciding due-ness and advancing the watermark*;
all actual handling (resolve staff, render, dispatch) happens later in the
worker.

```python
def _scheduler_tick(db_path: str, app_state, now: datetime) -> None:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT a.*, t.workspace_id, t.name AS topic_name, w.name AS workspace_name"
            " FROM event_actions a"
            " JOIN topics t ON t.id = a.scope_id"
            " JOIN workspaces w ON w.id = t.workspace_id"
            " WHERE a.event_type='topic_scheduler'"
            "   AND a.enabled=1"
            "   AND t.archived_at IS NULL"
        ).fetchall()
        for row in rows:
            anchor = _parse_iso(row['last_fired_at']) or _parse_iso(row['created_at'])
            next_fire = croniter(row['cron_expr'], anchor).get_next(datetime)
            if next_fire > now:
                continue
            # Optimistic watermark: advance BEFORE enqueueing. If the worker
            # never gets to it (cancelled / process crash), the slot is lost.
            # Chosen because duplicate fires are worse than missed fires for
            # the dominant summary/digest use case (ADR-0013).
            try:
                _update_last_fired(conn, row['id'], next_fire)
            except Exception:
                LOGGER.exception(
                    "event_action.scheduler_watermark_failed id=%s",
                    row['id'],
                )
                continue
            try:
                emit_event(
                    app_state=app_state,
                    event_type='topic_scheduler',
                    topic_id=row['scope_id'],
                    workspace_id=row['workspace_id'],
                    variables={
                        'topic_name': row['topic_name'],
                        'workspace_name': row['workspace_name'],
                    },
                    scheduler_slot=next_fire,
                    scheduler_action_id=row['id'],
                )
            except Exception:
                LOGGER.exception(
                    "event_action.scheduler_emit_failed id=%s",
                    row['id'],
                )
    finally:
        conn.close()
```

The tick does **not** call `dispatch_to_staff` — it only advances the
watermark and enqueues. That's the entire point of the queue + worker
split: the 60 s background thread stays cheap and predictable, and slow
agent dispatches do not block the next tick.

#### "Is this action due?" — exact rule

Let `now` = the moment the tick reads its clock. Let `anchor` be the stored
`last_fired_at` (or the action's `created_at` if `last_fired_at IS NULL`).
Compute `next_fire = croniter(cron_expr, anchor).get_next(datetime)`.

Rule: **enqueue iff `next_fire <= now`**, and atomically set
`last_fired_at = next_fire` (not `now`) before the enqueue. Setting to
`next_fire` rather than `now` prevents drift and ensures that a tick delayed
beyond a single cron interval still advances the watermark by exactly one
slot.

#### Watermark policy — optimistic, tick-side

The scheduler tick advances `last_fired_at` *before* it calls `emit_event`.
The worker does not touch `last_fired_at` for scheduler events. Trade-offs:

- **Loss on failure.** If the worker is cancelled at shutdown, the process
  crashes after the watermark write, or `emit_event` fails to enqueue, the
  slot is silently dropped. The next tick computes `next_fire` from the
  already-advanced `last_fired_at` and waits for the *following* slot.
- **No duplicates from slow workers.** Even if the worker lags by minutes,
  the next tick sees the advanced watermark and does not re-enqueue the
  same slot.
- **No in-memory dedup state needed.** All bookkeeping lives in SQLite,
  visible to operators and survives restarts cleanly.

Alternative considered: worker writes the watermark on success, tick keeps
an in-memory `(action_id, slot)` set of in-flight enqueues to suppress
duplicates within the process. Rejected — it adds a code path that doesn't
help across process restarts (the set is ephemeral) and trades a known
failure mode (silent missed fire on crash) for a more complex one
(duplicate fire if the in-memory set is dropped). For the dominant
summary/digest use case, missed fires are recoverable by manual re-trigger;
duplicate fires generate an extra agent run and an extra message in the
topic that the user has to clean up.

`scheduler_slot` and `scheduler_action_id` are passed through to the worker
purely so it can log them (`event_worker scheduler_action=… slot=…`) — no
state-mutation responsibility on the worker side for scheduler events.

#### Catch-up policy

If the loop has been paused for `K` minutes and `cron_expr='* * * * *'`,
the rule above will enqueue the action *once* this tick (advancing
`last_fired_at` by exactly one minute) and again next tick, and so on. We
deliberately **do not** burst-enqueue all `K` missed slots in the same tick
— that would flood the queue (and ultimately the agent) and is rarely what
operators want for an "every minute reminder".

If the operator wants strict "fire for every missed slot", they get it by
running the master without long pauses; sub-minute precision and lossless
catch-up are out of scope (ADR-0013).

#### Archived topics

`topic_scheduler` actions whose `scope_id` references an archived topic
**must not fire**. The tick query JOINs `topics` and filters on
`archived_at IS NULL`. The action row is *not* deleted on archive —
unarchiving (if/when that surfaces) restores firing. `topic_archive` itself
fires once on the archive transition, before the JOIN gate takes effect; this
is the design (the archive event is the last meaningful moment for the
topic).

#### Concurrent firings

The 60 s loop is single-threaded (one Python thread per master process,
fronted by `threading.Event.wait(60)`). Two `_scheduler_tick` calls cannot
overlap. The watermark-update is `UPDATE event_actions SET last_fired_at=?
WHERE id=?` and runs inside the same tick that read the row; SQLite
serialises that trivially. The worker is also single-threaded (concurrency
1), so no two events for the same action are in flight at once.

The remaining edge case is *worker starvation*: if a single event takes
many minutes to dispatch (an agent that hangs), the queue grows but the
scheduler tick keeps advancing watermarks and enqueueing new slots. There
is no per-action backpressure. See Open Questions.

### 7. API surface

All endpoints live under `/api` to match existing routers. No auth — the
codebase is single-user self-hosted (consistent with all other endpoints in
ADR-0009/0010/0011).

```
GET    /api/workspaces/{wid}/topics/{tid}/event-actions
POST   /api/workspaces/{wid}/topics/{tid}/event-actions
GET    /api/workspaces/{wid}/topics/{tid}/event-actions/{id}
PATCH  /api/workspaces/{wid}/topics/{tid}/event-actions/{id}
DELETE /api/workspaces/{wid}/topics/{tid}/event-actions/{id}
```

Request/response shape (Pydantic):

```python
class EventActionIn(BaseModel):
    event_type: Literal['topic_message_sent','topic_message_received','topic_scheduler','topic_archive']
    staff_name: str
    prompt_template: str
    timing: Literal['before','after'] | None = None
    cron_expr: str | None = None
    enabled: bool = True

class EventActionOut(EventActionIn):
    id: str
    scope_type: Literal['topic']
    scope_id: str
    last_fired_at: str | None
    created_at: str
    updated_at: str
```

Validation on POST/PATCH (mirroring DB CHECK constraints, but with friendly errors):
- `event_type='topic_scheduler'` ⇒ `cron_expr` required, `timing` must be null.
- `event_type='topic_message_sent'` ⇒ `timing` required (`before` or `after`),
  `cron_expr` must be null.
- `event_type` in `{topic_message_received, topic_archive}` ⇒ `timing` null
  or `'after'`, `cron_expr` null.
- `cron_expr` is parsed with `croniter.is_valid(cron_expr)` and rejected with
  422 if invalid.
- `staff_name` is **not** validated against the staff cascade at write time —
  staffs come and go, and a topic-scope action may legitimately reference a
  global staff that is created later. Resolution happens at fire time
  (graceful skip if missing, see §4).

PATCH semantics: any subset of fields can be updated; `id`, `scope_type`,
`scope_id`, `created_at`, `last_fired_at` are read-only.

### 8. Frontend

A new card on the topic settings panel, mirroring the staff card layout in
`frontend/src/views/WorkspaceDetail.vue` (lines ~89–180). Reachable from the
topic chat view (`TopicChat.vue`) via a "Topic settings" affordance.

Layout:

```
┌── Event actions ─────────────────────────────────── [+ Add action] ──┐
│ Trigger configured staffs when something happens in this topic.       │
│                                                                       │
│ [enabled] when @staff prompt-snippet-preview… last fired   [Edit][✕]  │
│ [enabled] cron @staff prompt-snippet-preview… last fired   [Edit][✕]  │
└───────────────────────────────────────────────────────────────────────┘
```

The form fields drive validation client-side; DB CHECK gives the
defence-in-depth. UI help text next to the prompt template input documents
the variable list per chosen `event_type` and `{{` escaping.

Pattern mirrors `WorkspaceDetail.vue`'s staff form: single `ref` for the form
state, separate `editingActionId` ref, save handler does POST or PATCH based
on whether an id is set, error string surfaces under the submit button.

### 9. Migration

The change is a single new table — no data migration, no `ALTER` step. Add to
`_SCHEMA` in `db.py` (sqlite is `CREATE TABLE IF NOT EXISTS`-driven there)
and add the two indexes inside `init_db` after the schema script runs.
Existing deployments pick up the new table on next process start. No
follow-up cleanup is needed.

There is no schema for "staff_name references staffs.id" because (a) staff
names are not unique across scopes — they can collide between topic, workspace
and global — and (b) the cascade resolution at fire time is the source of
truth. A FK would force a hard scope choice we do not want.

## Alternatives Considered

### Internal pub/sub bus

A general `EventBus.subscribe(event_type, handler)` would let event_actions
register as one subscriber among many.

Rejected for v1. We have four hard-coded emit sites and one consumer
(`event_actions`). Adding the bus introduces ordering, error-handling, and
discoverability questions for zero benefit at this scale. The chosen
queue-plus-worker is a degenerate case of a bus with exactly one consumer;
the rejection rationale is about *making subscription pluggable*, not about
queueing.

### Two-flavour helper (`emit_event_async` + `emit_event_threadsafe`)

An earlier draft of this design proposed two emitter functions sharing a
DB-and-render core: `emit_event_async` for the asyncio loop thread (request
handlers, `topics.delete_topic`) and `emit_event_threadsafe` for the MQTT
and scheduler threads. Each call site picked the flavour matching its
thread context, and both flavours did the lookup → resolve → render →
dispatch synchronously inline. The naming followed the existing
`hub.broadcast` / `hub.broadcast_threadsafe` pattern.

Rejected. Three problems:

1. **Emission and handling are intermixed at every site.** Each emit site
   ends up paying for the full DB query and dispatch chain on its own
   thread. A slow agent dispatch on the MQTT thread blocks MQTT message
   processing; on the request thread it blocks the HTTP response.
2. **No single point for cross-cutting concerns.** Adding observability
   (queue depth, handler latency), retry, dedup, or rate limiting means
   patching both flavours in lockstep.
3. **Two parallel code paths.** The two flavours sharing a "core" still
   means two different async-vs-sync transcripts to keep correct as the
   handler grows.

The queue-plus-worker design replaces both flavours with a single
threadsafe `emit_event` that is always non-blocking and always returns
immediately, and centralises handling in one async worker.

### One table per event type

`topic_message_actions`, `scheduler_actions`, `archive_actions`, etc. Each
table has only its relevant columns; CHECK constraints become trivial.

Rejected. The shared columns (`staff_name`, `prompt_template`, `enabled`,
`scope_*`, audit timestamps) outnumber the kind-specific ones. The CRUD code
would multiply by N, and the API surface would balloon. Narrow + CHECK gives
the same correctness guarantee with one-quarter the code.

### Single JSON config blob per event_action

`event_actions(event_type, scope_*, config_json)` where `config_json` holds
`{staff_name, prompt_template, timing|cron_expr, enabled, ...}`.

Rejected. Loses SQL filterability (especially "list all scheduler actions due
now") and pushes validation entirely into Python. Schema CHECK at write time
is cheap insurance.

### External cron + webhook for scheduler

Run host cron, have it `POST /api/.../trigger` to fire scheduler actions.

Rejected. Couples deployment topology to host config; no benefit when the
60 s loop already exists; loses topic-state visibility (the trigger needs to
know which topics are archived, what the workspace_name is).

### Per-message no-retrigger flag instead of `sender="event"`

A `boolean` column on `messages` instead of widening the `sender` enum.

Rejected. The sender is genuinely different — surfacing it in the chat UI is
useful (users will want to see "this came from a scheduler"), and the
existing `messages.sender` column already discriminates user vs. agent, so a
third value fits naturally.

## Migration Plan

This is a feature addition with no behaviour change for existing deployments.

1. **Schema:** add `event_actions` table + indexes to `_SCHEMA` in
   `src/master/db.py`. No row-level migration required.
2. **Backend:**
   - Extract `dispatch_to_staff` from `send_message` (refactor; behaviour
     preserved). Add unit test that the user-message path still works
     identically.
   - Add `src/master/event_actions.py` with the `event_actions` CRUD router
     (mounted under `/api/workspaces/{wid}/topics/{tid}/event-actions`),
     `EventActionIn`/`EventActionOut`, and the single `emit_event` helper.
   - Add `src/master/event_worker.py` (or extend `event_actions.py`) with
     `event_worker(app_state)` and `_handle_event(app_state, event)`.
   - Create `app.state.event_queue = asyncio.Queue()` and
     `app.state.event_loop = asyncio.get_running_loop()` in the FastAPI
     lifespan startup, and start the worker via
     `asyncio.create_task(event_worker(app.state))`. Cancel the task on
     lifespan shutdown.
   - Wire `emit_event(...)` calls into the four emit sites listed in §4
     and into `_scheduler_tick` (§6).
   - Add `croniter` to `requirements.txt`.
3. **Frontend:** add the event-actions card under the topic settings panel
   (entry point added to `TopicChat.vue` if no topic-settings page exists yet
   — see Open Questions).
4. **Tests:** unit tests for CRUD + validation; integration test proving
   shared-session semantics (event-triggered `@reviewer` resumes the same
   session as user-triggered `@reviewer`); scheduler tick test with a frozen
   clock.
5. **Docs:** update `docs/references/api.md` with the new endpoints; add
   `docs/guides/event-actions.md` with example templates and the variable
   list.

Rollback: drop the `event_actions` rows + table; revert the
`dispatch_to_staff` extraction. The user-message path is dead-code-equivalent
to today's `send_message` and is safe to revert independently.

## Open Questions

- [ ] **Topic settings page entry point.** Does a topic settings panel
      already exist, or is the entry point a new disclosure on `TopicChat.vue`?
      `WorkspaceDetail.vue` has a Staff section per workspace; there is no
      analogous per-topic settings page yet. Owner: frontend engineer to
      decide between (a) a side panel on `TopicChat.vue`, (b) a separate
      `/workspaces/{wid}/topics/{tid}/settings` route, or (c) inline expand
      from the topic header. Recommend (b) for symmetry with workspace
      settings; (a) acceptable as v1 shortcut.
- [ ] **Should `topic_archive` events fire after the topic is hidden from
      the active list?** The current proposal fires them after `archived_at`
      is committed, meaning the resulting agent reply lands on an
      already-archived topic. The frontend lists archived topics under
      Archived Topics, so the message is reachable; but if operators expect
      "summarise then archive" rather than "archive then summarise", we may
      need to flip the order. Owner: confirm with users — file a follow-up
      if behaviour needs to change.
- [ ] **Should `cron_expr` allow timezone-aware expressions?**
      `croniter` defaults to naive local time when given a naive datetime.
      The master process runs in container TZ (UTC by convention here).
      Document that `cron_expr` is interpreted in UTC; revisit if users need
      per-action TZ.
- [ ] **Maximum prompt template length.** No hard limit currently; SQLite
      `TEXT` is unbounded. Probably fine for v1; revisit if abuse appears.
- [ ] **`enabled` toggle endpoint.** Should there be a dedicated
      `POST /api/.../event-actions/{id}/disable` path, or is `PATCH {enabled:
      false}` sufficient? Recommend the latter — fewer endpoints, same effect.
- [ ] **Worker starvation under a stuck dispatch.** Concurrency is 1, the
      queue is unbounded, and the scheduler tick keeps advancing watermarks
      and enqueueing new slots independent of the worker's progress. If a
      single `dispatch_to_staff` call hangs (agent container wedged, MQTT
      backpressure), the queue grows without bound and every other event in
      the system stalls behind it. Owner: engineer to add (a) a watchdog
      timeout around `dispatch_to_staff` inside `_handle_event` (kill the
      handler after N seconds, log, move on) and (b) a `WARN` log when
      `queue.qsize()` crosses a threshold (e.g. 50). Both can ship with v1;
      a bounded queue with a drop-oldest policy is a follow-up if abuse
      appears. Recommend timeout = 60 s, qsize warn = 50.
- [ ] **Worker observability surface.** Should the worker expose its queue
      depth and last-handled-event timestamp via an admin endpoint
      (`GET /api/admin/event-worker/status`), or is a periodic log line
      enough? Recommend log-only for v1; add the endpoint when there is a
      diagnosis flow that needs it.

## Test plan key cases

These are the cases the design must remain testable against. Full test plan
goes in `docs/test-plans/event-based-staff-action.md` (tester's deliverable).

- CRUD round-trip per event_type.
- DB CHECK constraint negative cases (missing cron_expr for scheduler;
  timing='before' on `topic_message_received`; etc.).
- Variable substitution: known variables substituted; unknown placeholder
  preserved literally; `{{` escapes a literal brace.
- Loop prevention: a `topic_message_sent` action that publishes a message
  via its staff does **not** retrigger itself (because the new message has
  `sender='event'`).
- Session sharing: `@reviewer` invoked first via user message and second via
  `topic_scheduler` lands on the same `staff_sessions` row and uses
  `--resume` for the second call.
- Multi-action fanout: two enabled actions on the same trigger both fire;
  failure of one does not block the other.
- Disabled actions are skipped (no MQTT publish).
- Deleted-staff handling: action references a staff name that resolves to
  None at fire time → log + skip, no exception.
- Archived topic: scheduler actions for archived topics are not fired even
  if cron matches.
- Scheduler watermark: after a 5 s pause, action is not fire-eligible; after
  a 65 s pause and `* * * * *`, action fires once and `last_fired_at`
  advances by exactly one minute (the watermark is advanced by the tick
  before enqueueing, so the assertion holds even if the worker has not yet
  picked up the event).
- Scheduler optimistic-watermark loss: if the worker is cancelled between
  enqueue and dispatch, `last_fired_at` is still advanced and the *next*
  cron slot is the one that fires — assert this is the documented
  behaviour. No duplicate fire of the lost slot.
- `emit_event` thread-safety: emitting from a synthesised non-loop thread
  (simulating MQTT-thread or scheduler-thread context) places the event on
  the queue and the worker handles it; emitting from the loop thread does
  not deadlock and does not require `call_soon_threadsafe` to be a no-op.
- Worker error isolation: an event whose handler raises (e.g. forced
  exception in the resolve step) is logged at `event_worker.handle_failed`
  and the worker keeps draining; the next queued event is handled
  normally.
- Per-action error isolation inside an event: two enabled actions on the
  same event, the first raises during render — the second still dispatches.
- Worker shutdown loss: events queued but not yet handled at lifespan
  shutdown are dropped without raising; the test asserts the worker task
  is cancelled cleanly and no exceptions propagate.
- Single-worker ordering: emit events A then B from the same thread; the
  worker handles A before B (FIFO).
