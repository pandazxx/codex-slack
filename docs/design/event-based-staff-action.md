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
4. **Emit `topic_message_sent` (before).**
5. Call `dispatch_to_staff(..., sender='user', raw_text=text, attachments=...)`.
6. **Emit `topic_message_sent` (after).**

The event emitter (see §4) calls `dispatch_to_staff` directly with
`sender='event'` for each matching action. Session sharing falls out: both
paths feed the same `staff` row into `_staff_session_key()` and
`_get_staff_session()`, so an event-triggered call and a user-triggered call
land on the same `staff_sessions` row and the same `--resume <uuid>` flag.

### 4. Event emission points

```mermaid
sequenceDiagram
    participant U as User
    participant API as POST /messages
    participant E as event emitter
    participant D as dispatch_to_staff
    participant MQTT
    participant Agent
    participant MC as mqtt_client._on_message
    participant T as DELETE /topics/{id}
    participant BG as 60s background loop

    U->>API: text="..."
    API->>E: emit topic_message_sent (before)
    E-->>D: matching actions × N (sender=event)
    API->>D: user dispatch (sender=user)
    D->>MQTT: publish prompt
    API->>E: emit topic_message_sent (after)
    Agent->>MQTT: response
    MQTT->>MC: _on_message
    MC->>MC: _save_agent_response
    MC->>E: emit topic_message_received (after)
    E-->>D: matching actions × N (sender=event)

    T->>T: archive topic
    T->>E: emit topic_archive (after)
    E-->>D: matching actions × N

    BG->>BG: each minute
    BG->>E: scan scheduler actions
    E-->>D: due actions × N
```

Concrete sites:

| Hook | File | Insertion point |
|---|---|---|
| `topic_message_sent` (before) | `messages.py:send_message` | After staff resolution, **before** `mqtt.publish` |
| `topic_message_sent` (after)  | `messages.py:send_message` | After `mqtt.publish` returns |
| `topic_message_received`      | `mqtt_client.py:_on_message` (`msg_type == "response"` branch) | After `_save_agent_response` and `_record_agent_response`, alongside the existing `notify.notify_reply` call |
| `topic_archive`               | `topics.py:delete_topic` | After the `UPDATE topics SET archived_at = ?` commit |
| `topic_scheduler`             | `main.py:_background_tasks` | New per-minute pass over scheduler actions, see §6 |

The emitter is a single function:

```python
def emit_event(
    *,
    app_state,
    event_type: str,
    topic_id: str,
    workspace_id: str,
    timing: str | None = None,   # 'before'|'after'|None
    variables: dict[str, str],
) -> None:
    """Look up matching event_actions and dispatch each. Errors are logged
    per-action and never propagate to the caller."""
```

Emitter behaviour:
- Selects rows: `scope_type='topic' AND scope_id=topic_id AND event_type=?
  AND enabled=1 AND (timing IS NULL OR timing=?)`.
- For each row, in DB-row order:
  1. Resolve the staff via `resolve_staff(conn, staff_name, workspace_id, topic_id)`.
  2. If staff is `None` → log `event_action.staff_missing` and skip.
  3. Render the prompt with `render_template(template, variables)`.
  4. Call `dispatch_to_staff(..., sender='event', raw_text=rendered)`.
  5. Wrap each iteration in `try/except` — one bad action does not skip the
     others.

`emit_event` is called synchronously from request paths (FastAPI handlers run
on the asyncio loop, so `dispatch_to_staff` is async-callable from
`send_message` and `topics.delete_topic`). From the MQTT thread
(`_on_message`) and the background scheduler thread, the emitter is called
synchronously and uses `hub.broadcast_threadsafe` plus `mqtt.publish` (both
already documented as thread-safe in this codebase). Concretely the helper
will have two flavours, `emit_event_async` and `emit_event_threadsafe`,
sharing the same DB-and-render core; the names match the existing
`hub.broadcast` / `hub.broadcast_threadsafe` pattern in `ws_hub.py`.

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
~lines 65–150). New pass at the top of each tick, before per-workspace work:

```python
def _scheduler_tick(db_path: str, app_state, now: datetime) -> None:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE event_type='topic_scheduler' AND enabled=1"
        ).fetchall()
        # Pre-filter archived topics in SQL to avoid per-row Python work:
        # JOIN topics WHERE archived_at IS NULL (see "Archived topics" below)
        ...
    finally:
        conn.close()

    for row in due_rows:
        try:
            variables = {"topic_name": ..., "workspace_name": ...}
            emit_event_threadsafe(
                app_state=app_state,
                event_type='topic_scheduler',
                topic_id=row['scope_id'],
                workspace_id=...,    # JOIN through topics
                variables=variables,
            )
            _update_last_fired(conn, row['id'], now)
        except Exception:
            LOGGER.exception("event_action.scheduler_fire_failed id=%s", row['id'])
```

#### "Is this action due?" — exact rule

Let `now` = the moment the tick reads its clock. Let `last_fired_at` be the
stored watermark (or the action's `created_at` if `last_fired_at IS NULL`).
Compute `next_fire = croniter(cron_expr, last_fired_at).get_next(datetime)`.

Rule: **fire iff `next_fire <= now`**. After a successful fire, set
`last_fired_at = next_fire` (not `now`) — this prevents drift and ensures
that a tick delayed beyond a single cron interval still advances the
watermark by exactly one slot.

#### Catch-up policy

If the loop has been paused for `K` minutes and `cron_expr='* * * * *'`,
the rule above will fire the action *once* this tick (advancing
`last_fired_at` by exactly one minute) and again next tick, and so on. We
deliberately **do not** burst-fire all `K` missed slots in the same tick —
that would flood the agent and is rarely what operators want for an "every
minute reminder".

If the operator wants strict "fire for every missed slot", they get it by
running the master without long pauses; sub-minute precision and lossless
catch-up are out of scope (ADR-0013).

#### Archived topics

`topic_scheduler` actions whose `scope_id` references an archived topic
**must not fire**. The query JOINs `topics` and filters on
`topics.archived_at IS NULL`. The action row is *not* deleted on archive —
unarchiving (if/when that surfaces) restores firing. `topic_archive` itself
fires once on the archive transition, before the JOIN gate takes effect; this
is the design (the archive event is the last meaningful moment for the
topic).

#### Concurrent firings (the failure mode you asked about)

The 60 s loop is single-threaded (one Python thread per master process,
fronted by `threading.Event.wait(60)`). Two `_scheduler_tick` calls cannot
overlap. The watermark-update is `UPDATE event_actions SET last_fired_at=?
WHERE id=?` inside the same tick that read the row; SQLite serialises that
trivially. So the only way an action could fire twice for the same minute is
if the tick body itself loops — which it does not.

The remaining edge case is *master restart*: if the master crashes after
publishing to MQTT but before updating `last_fired_at`, the action will fire
again on the next tick after restart. Acceptance: cron actions are
informational/idempotent in spirit (summaries, digests). Operators who need
exactly-once should use a stronger trigger (manual or `topic_archive`).

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
discoverability questions for zero benefit at this scale.

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
     `EventActionIn`/`EventActionOut`, and the `emit_event_async` /
     `emit_event_threadsafe` helpers.
   - Wire `emit_event_*` calls into the four emit sites listed in §4.
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
  advances by exactly one minute.
- Master restart in-flight: scheduler that fired but didn't update watermark
  fires once more — assert this is the documented behaviour, not a bug.
