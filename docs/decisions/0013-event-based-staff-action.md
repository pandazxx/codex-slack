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

**Chosen:** **A + P + X + S1 + W1.** Concretely:

1. **A new `event_actions` table** with narrow columns (`event_type`,
   `scope_type`, `scope_id`, `staff_name`, `prompt_template`, `timing`,
   `cron_expr`, `last_fired_at`, `enabled`). CHECK constraints enforce
   field/event-type validity. No JSON blob; no per-event-type table.
2. **Hard-wired emission points.** No bus. The four sites are:

   | event_type | emit site (file:func) | timing |
   |---|---|---|
   | `topic_message_sent` | `messages.py:send_message` | `before` and `after` MQTT publish |
   | `topic_message_received` | `mqtt_client.py:_on_message` after `_save_agent_response` | `after` only |
   | `topic_archive` | `topics.py:delete_topic` after `archived_at` is set | `after` only |
   | `topic_scheduler` | `main.py:_background_tasks` 60 s loop | N/A |

   At each site, master loads matching `event_actions` rows
   (scope+event_type+enabled) and calls a shared `_dispatch_to_staff()` helper
   (extracted from `send_message`) for each one in turn. Fanout is sequential
   from the emit site's perspective but produces independent MQTT publishes
   and independent dispatch records, so the agent sees them as parallel.
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
5. **Scheduler reuses the 60 s background loop.** Add `croniter` to
   `requirements.txt`. Per-action `last_fired_at` is the bookkeeping anchor;
   "due" is defined as *the cron's next match strictly after `last_fired_at`
   has already passed (relative to now)*. See "Concurrent firings" in the
   design doc for the exact rule and edge cases.
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
