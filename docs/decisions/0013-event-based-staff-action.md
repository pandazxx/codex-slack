---
title: "ADR-0013: Event-based staff actions"
status: proposed
date: 2026-05-07
decision-makers: [architect, engineer]
consulted: [tester, sre]
informed: [doc-writer, users]
---

## Context and Problem Statement

Today every Staff invocation is initiated by a user typing in the topic chat
(per ADR-0009 §7). Several recurring asks — "auto-summarise this topic when it
goes idle", "post a daily digest", "kick off a code-review staff whenever a
human posts" — all share the same shape: *something happens in the system, run
a configured Staff with a templated prompt*. See GitHub issue
[#143](https://github.com/pandazxx/codex-slack/issues/143).

We want a single, declarative mechanism for binding *events* in the system to
*Staff invocations*, without inventing a parallel dispatch path or weakening
the session model.

## Decision Drivers

- **Reuse the dispatch path.** User messages already resolve a Staff, derive a
  session UUID, and publish a prompt to MQTT (`src/master/messages.py`). Event
  triggers must travel the same pipe so session sharing, MQTT contracts, and
  agent behaviour fall out automatically.
- **Session sharing is non-negotiable.** An event-triggered run of `@reviewer`
  in topic T must be *the same conversation* as a user-triggered `@reviewer` in
  topic T. The existing `staff.session_scope` rules (ADR-0009 §3) already
  encode this; we must not bypass them.
- **Loop-safe.** A reaction to "user sent a message" can itself send a message
  (via the agent reply path). The system must not feed event-triggered output
  back into the same trigger.
- **Composable / observable.** Multiple actions on the same event should fire
  independently. Failure of one must not block the others.
- **Single point for cross-cutting concerns.** Event emission happens from
  three different thread contexts (FastAPI loop, MQTT thread, scheduler
  thread). Whatever shape we pick must give us *one* place to add logging,
  retry, dedup, or rate limiting later — not three or four parallel code
  paths to keep in sync.
- **Minimum surface.** Ship one mechanism that covers the common cases
  (message hooks, scheduler, archive); leave workspace-scope events for a
  follow-up once the output channel is designed.
- **Operability.** Operators must be able to disable a misbehaving event
  action without deleting it, and see when it last fired.

## Considered Options

### Storage shape

A. **One narrow table, separate columns** for the timing/cron fields, with
   CHECK constraints validating event-type/field combinations.
B. **One generic table, single `config_json` blob** holding all
   per-event-type knobs.
C. **One table per event type** (`topic_message_actions`, `scheduler_actions`,
   etc.).

### Trigger model

P. **Event-emission points hard-wired in code paths.** Each known emit site
   (message hook, archive, scheduler tick) iterates matching `event_actions`
   and dispatches. No event bus, no subscribers.
Q. **Internal pub/sub bus.** Code emits semantic events; subscribers register
   handlers. `event_actions` is one such subscriber.

### Handler concurrency model

H1. **Single-point queue + one async worker.** All emit sites call one
    `emit_event(...)` that enqueues onto a shared `asyncio.Queue`. A single
    long-running worker task drains the queue, looks up matching actions,
    resolves staff, renders templates, and calls `dispatch_to_staff`.
H2. **Two-flavour helper (`emit_event_async` + `emit_event_threadsafe`).**
    Each emit site calls the flavour matching its thread context; both flavours
    do the lookup → resolve → render → dispatch synchronously inline.
H3. **Per-emit-site direct dispatch.** Each emit site iterates rows and calls
    `dispatch_to_staff` directly with its own thread-context glue.

### Loop prevention

X. **`sender` column on `messages`.** Event-triggered messages carry
   `sender="event"` and the message hooks gate on `sender="user"` /
   `sender="agent"` only.
Y. **Per-message "do not retrigger" flag.** Event firings tag the message with
   `_no_retrigger=true`; hooks check the flag.
Z. **Caller-passed depth counter.** Each dispatch carries a depth; the event
   layer drops anything beyond depth 1.

### Scheduler implementation

S1. **`croniter` in the existing 60 s loop.** Each tick scans
    `event_actions` where `event_type='topic_scheduler'` and asks `croniter`
    whether the action is due since `last_fired_at`.
S2. **A separate scheduler thread/process** with sub-minute resolution.
S3. **External cron (host-level)** posting to a webhook.

### Workspace-scope events

W1. **Defer.** Ship topic scope only; schema admits `scope_type='workspace'`
    but only the topic value is implemented.
W2. **Ship now.** Define a workspace-level output channel (which topic does
    the reply go to? a synthetic "system" topic? a dead-letter?).

## Decision Outcome

**Chosen:** **A + P + H1 + X + S1 + W1.** Concretely:

1. **A new `event_actions` table** with narrow columns (`event_type`,
   `scope_type`, `scope_id`, `staff_name`, `prompt_template`, `timing`,
   `cron_expr`, `last_fired_at`, `enabled`). CHECK constraints enforce
   field/event-type validity. No JSON blob; no per-event-type table.
2. **Hard-wired emission points, queue + single async worker for handling.**
   No bus. The four sites are:

   | event_type | emit site (file:func) | timing |
   |---|---|---|
   | `topic_message_sent` | `messages.py:send_message` | `before` and `after` MQTT publish |
   | `topic_message_received` | `mqtt_client.py:_on_message` after `_save_agent_response` | `after` only |
   | `topic_archive` | `topics.py:delete_topic` after `archived_at` is set | `after` only |
   | `topic_scheduler` | `main.py:_background_tasks` 60 s loop | N/A |

   Each site builds a tiny event dict (event_type, scope ids, variables,
   optional timing) and calls a single `emit_event(...)` that pushes onto an
   `asyncio.Queue`. `emit_event` is safe to call from any thread (FastAPI loop,
   MQTT thread, scheduler thread) and returns immediately. A single
   long-running async worker (`event_worker`, started from FastAPI lifespan)
   drains the queue: for each event it loads matching `event_actions` rows,
   resolves staff, renders the template, and calls `dispatch_to_staff`
   (extracted from `send_message`). The worker is the only consumer — exactly
   one event is handled at a time across the whole process. Per-action errors
   inside one event are caught and logged so other actions in the same event,
   and subsequent events, are unaffected.
3. **Loop prevention via `sender="event"`.** Event-triggered messages are
   inserted into `messages` with `sender="event"`. Hooks gate strictly:
   - `topic_message_sent` fires only when `sender="user"`.
   - `topic_message_received` fires only when `sender="agent"`.

   Event-triggered messages therefore never re-fire either message hook.
   Scheduler and archive events do not insert any pre-existing message and
   are unaffected.
4. **Variable substitution via `str.format_map(defaultdict(...))`.**
   Templates use `{variable}` syntax. Unknown placeholders are left as the
   literal `{name}` string — a misconfigured template logs a warning but does
   not crash dispatch. Variables per event type are listed in the design doc.
5. **Scheduler reuses the 60 s background loop, watermark advanced
   optimistically by the tick.** Add `croniter` to `requirements.txt`.
   Per-action `last_fired_at` is the bookkeeping anchor; "due" is defined as
   *the cron's next match strictly after `last_fired_at` has already passed
   (relative to now)*. The scheduler tick updates `last_fired_at = next_fire`
   *before* enqueueing the event — it does not wait for the worker to
   acknowledge. Trade-off: if the worker is cancelled or the process crashes
   between enqueue and dispatch, the slot is silently lost; in exchange we
   avoid duplicate fires when the worker is slow and we keep the tick
   self-contained. Chosen because the dominant scheduler use cases are
   summaries and digests, where a missed fire is annoying and a duplicate
   fire is materially worse (operators see the agent run twice and an extra
   message land in the topic). See the design doc §6 for the exact rule and
   edge cases.
6. **Workspace-scope events are deferred.** The schema admits `scope_type` so
   we do not need a future migration, but only `scope_type='topic'` is
   implemented in v1. Workspace-scope events require an output-channel
   decision — in particular *which topic does the agent reply to?* — that is
   out of scope for this ADR. A follow-up ADR will pick that up; until then
   the API will reject `scope_type='workspace'` with a 422.
7. **Full CRUD UI under topic settings**, mirroring the existing Staff CRUD
   panel in `frontend/src/views/WorkspaceDetail.vue` (list, create form,
   inline edit, delete, enable/disable toggle).

### Alignment with prior ADRs

- **ADR-0009 (Staff system):** Event actions are *consumers* of the Staff
  cascade. They reference a `staff_name`; resolution at fire time uses the
  same `resolve_staff(conn, name, workspace_id, topic_id)` cascade
  (topic → workspace → global). If the staff has been deleted, the action is
  skipped with a logged warning (see "Empty/disabled staff" in the design doc).
- **ADR-0011 (Outbound notifications):** Notifications fire from
  `mqtt_client._on_message` *before* the new `topic_message_received` event
  hook. The two are independent: a notification is informational; an event
  action is a follow-up dispatch. Both can run for the same agent reply.

### Consequences

- **Good**
  - One declarative table covers message hooks, scheduler, and archive
    triggers — no special-case code paths per use case.
  - Session sharing is automatic because event dispatch and user dispatch
    converge on the same helper and the same `_staff_session_key()` resolution.
  - Loop-safety is structural (`sender="event"` plus narrow gate predicates)
    rather than relying on caller discipline.
  - Adding new event types in the future is a code-only change at one emit
    site plus one row in a `_VALID_EVENT_TYPES` set; the table and API need
    no schema work as long as the new event fits the existing column shape.
  - Operators can disable an action without deleting it (`enabled=0`) and see
    last-fire times (`last_fired_at`) for diagnosis.
  - One worker, one queue: emission is the same one-line call from all three
    thread contexts; logging, retry, dedup, or rate-limit can be added in
    exactly one place (`event_worker`) when needed.
  - Sequential handling (concurrency=1) eliminates races between the
    MQTT thread, request thread, and scheduler thread firing simultaneously
    against the same `event_actions` row, with no explicit locks required.
- **Bad / accepted tradeoffs**
  - Hard-wired emit sites mean adding an event type is a code change. Worth
    it for v1 — a generic bus would over-engineer the surface for four call
    sites.
  - Scheduler resolution is minute-level. Sub-minute cron expressions are
    rejected at API write time, not silently ignored.
  - `croniter` is a new runtime dependency. It is small (single-package, no
    transitive deps) and the canonical Python cron parser; rolling our own
    saves nothing.
  - Workspace-scope events are deferred. Schema is forward-compatible
    (`scope_type` already admits `'workspace'`), so the follow-up will not
    require a migration.
  - A long-paused scheduler can fire missed-cron actions late but only once
    per "missed window" — see the catch-up rule in the design doc. Operators
    who want strict at-most-once-per-real-minute semantics will not get them.
  - **Worker shutdown is best-effort.** Events queued but not yet handled at
    process shutdown are dropped. The queue is in-memory, no persistence.
    Acceptable: emission is human-rate (~1/s), and a missed event-triggered
    summary is recoverable by re-firing manually. Out of scope to add a
    durable outbox for v1.
  - **Scheduler optimistic watermark loses fires on worker failure.** The
    tick advances `last_fired_at` before enqueue, so a worker exception or
    process crash between enqueue and dispatch silently drops that slot.
    Chosen over the alternative (worker writes watermark on success, tick
    keeps an in-memory dedup set of pending `(action_id, slot)` pairs)
    because duplicates are worse than missed fires for the dominant
    summary/digest use case, and the dedup set adds complexity without
    helping across process restarts.
  - **Single-worker throughput cap.** With one worker, slow agent dispatches
    serialise the whole event queue. Under v1's human-rate emission this is
    fine; if the cap becomes visible (rare-but-possible during a flood),
    raise concurrency or shard the worker — both are local changes inside
    `event_worker` that don't affect emission sites.

### Confirmation

- Unit tests in `tests/master/test_event_actions.py` for:
  - CRUD round-trips (create/list/get/patch/delete) at topic scope.
  - Schema CHECK constraints (cron_expr required iff scheduler;
    timing='before' allowed only for topic_message_sent).
  - Variable substitution including unknown-placeholder safety.
  - Loop prevention: a `sender="event"` message does not fire
    `topic_message_sent`; a `sender="event"` agent reply does not exist (only
    real agent replies are `sender="agent"`), so the gate is structural.
- Integration test (`tests/master/test_event_dispatch.py`):
  - Topic with two enabled `topic_message_sent` actions → both fire on a user
    message, MQTT receives two prompt publishes, both share the user's session
    when the staffs are configured for `session_scope='topic'`.
  - One bad action (e.g. references a deleted staff, or template render
    raises) does not block sibling actions in the same event, and the worker
    keeps draining subsequent events.
  - `emit_event` called from a non-loop thread (simulated MQTT-thread
    context) is observed in the queue and handled by the worker.
- Scheduler test:
  - With `last_fired_at` 65 s ago and `cron_expr='* * * * *'`, action is due.
  - With `last_fired_at` 5 s ago, action is not due.
  - Archived topic does not fire scheduler actions even if cron matches.
- UAT (in feature-branch staging):
  - Configure a `topic_archive` action invoking `@summariser`; archive a topic;
    observe the summary message land in the topic before it is hidden from
    the active list. `automated`.
  - Configure a `topic_scheduler` action with `cron_expr='*/2 * * * *'`;
    wait 5 minutes; observe two or three fires. `needs-human` (wall-clock).

## Pros and Cons of the Options

### Storage shape

| Option | Pro | Con |
|---|---|---|
| A — narrow columns + CHECK (chosen) | SQL-queryable; CHECK catches invalid combos at write time; cheap migration | One column per future per-event knob |
| B — single JSON blob | Flexible | Validation moves to app layer; no SQL filter on `cron_expr`; opaque to ops |
| C — one table per event | Strict typing per kind | 4× the schema, 4× the API code; common fields duplicated |

### Trigger model

| Option | Pro | Con |
|---|---|---|
| P — hard-wired emit sites (chosen) | Trivial code path; explicit; no ordering surprises | Adding a new event type is a code change |
| Q — internal pub/sub bus | Extensible | Adds a layer without v1 demand; ordering and error semantics need design |

### Handler concurrency model

| Option | Pro | Con |
|---|---|---|
| H1 — queue + single async worker (chosen) | One emission API across all threads; one place for cross-cutting concerns; sequential handling eliminates races without locks | In-memory queue → shutdown lossiness; single worker caps throughput |
| H2 — two-flavour helper (`emit_event_async` + `emit_event_threadsafe`) | No background task; emission completes synchronously per site | Emission and handling intermixed at every site; no single point to add observability/retry/dedup; two parallel code paths to keep in sync |
| H3 — per-site direct dispatch | Simplest possible call sites | Duplicates the lookup→resolve→render→dispatch sequence at every site; thread-context glue scattered |

### Loop prevention

| Option | Pro | Con |
|---|---|---|
| X — `sender="event"` (chosen) | Single source of truth; visible in UI; structural gate on hooks | Adds a third sender enum value (already informally extensible) |
| Y — per-message no-retrigger flag | Self-contained per row | Extra column; relies on every emit site setting it |
| Z — depth counter | Generalises to N-level chains | Premature; v1 has no chained-event use case |

### Scheduler implementation

| Option | Pro | Con |
|---|---|---|
| S1 — `croniter` in 60 s loop (chosen) | Reuses existing thread; no new processes; minute resolution is enough | Sub-minute jobs not supported; loop pause = late fires |
| S2 — separate scheduler thread/process | Sub-minute precision possible | Extra moving part; coordination with stop_event; no demand |
| S3 — external cron → webhook | Battle-tested timing | Couples the system to host config; doesn't compose with topic-scope state |

### Workspace-scope events

| Option | Pro | Con |
|---|---|---|
| W1 — defer (chosen) | Ships v1 without inventing an output channel | Users wanting workspace-level cron must wait |
| W2 — ship now | Feature-complete | Forces a synthetic-topic / dead-letter design that has no clear answer yet |
