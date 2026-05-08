# Test Plan: Event-based Staff Actions

**Feature:** Event-based staff actions (ADR-0013)
**Design doc:** [docs/design/event-based-staff-action.md](../design/event-based-staff-action.md)
**ADR:** [docs/decisions/0013-event-based-staff-action.md](../decisions/0013-event-based-staff-action.md)
**Status:** scaffolded — test bodies deferred until engineer signals interfaces are stable
**Date:** 2026-05-08

---

## Interfaces this plan depends on

The following function and endpoint signatures are extracted from the design doc
(§3, §4, §6, §7). If the engineer changes any of these, update the corresponding
test cases and this section.

| Interface | Expected signature / shape | Design ref |
|---|---|---|
| `dispatch_to_staff` | `async (*, app_state, workspace_id, topic_id, staff, prompt_text, sender, raw_text=None, attachments=None) -> str` | §3 |
| `emit_event` | `(*, app_state, event_type, topic_id, workspace_id, timing=None, variables, scheduler_slot=None, scheduler_action_id=None) -> None` | §4 |
| `event_worker` | `async (app_state) -> None` — drains `app_state.event_queue`; sets `app_state.event_worker_last_progress` | §4 |
| `_scheduler_tick` | `(db_path, app_state, now_utc_aware: datetime) -> None` | §6 |
| `render_template` | `(template: str, variables: dict[str, str]) -> str` | §2 |
| `EventActionIn` | Pydantic: `event_type`, `staff_name`, `prompt_template`, `timing?`, `cron_expr?`, `enabled` | §7 |
| `EventActionOut` | Pydantic: adds `id`, `scope_type`, `scope_id`, `last_fired_at`, `last_run_at`, `last_run_status`, `last_run_output`, `created_at`, `updated_at` | §7 |
| REST endpoints | `GET/POST /api/workspaces/{wid}/topics/{tid}/event-actions`, `GET/PATCH/DELETE /api/.../event-actions/{id}` | §7 |

---

## Pass/Fail Criteria

A test **passes** when:
- The observed behaviour matches the expected result precisely.
- For `automated` tests: assertion is machine-verifiable without human input.
- For `needs-human` tests: a human reviewer has confirmed the expected visual or
  timing-dependent behaviour via the staging UI.

A test **fails** when any assertion is violated, an unexpected exception is raised,
or a `needs-human` case is declined during UAT signoff.

---

## Test Matrix

Cases are grouped by area. Each row carries:
- **ID** — used as the `pytest` node ID suffix in the skeleton file.
- **Kind** — `automated` or `needs-human`.
- **Priority** — `P0` (blocking), `P1` (high), `P2` (normal).

### Area 1: Schema / CRUD

Tests exercise the REST API and the underlying SQLite rows via `TestClient`.

| ID | Case | Kind | Priority |
|---|---|---|---|
| CRUD-01 | `GET` event-actions on new topic returns empty list | automated | P0 |
| CRUD-02 | `POST` `topic_message_sent` with `timing='before'` returns 201 with all required fields | automated | P0 |
| CRUD-03 | `POST` `topic_message_received` with no timing returns 201 | automated | P0 |
| CRUD-04 | `POST` `topic_scheduler` with valid `cron_expr` returns 201 | automated | P0 |
| CRUD-05 | `POST` `topic_archived` returns 201 | automated | P0 |
| CRUD-06 | `GET` by `id` returns the created row with matching fields | automated | P0 |
| CRUD-07 | `PATCH` `enabled=false` disables the action; subsequent GET reflects change | automated | P0 |
| CRUD-08 | `PATCH` `prompt_template` updates only that field; other fields unchanged | automated | P1 |
| CRUD-09 | `PATCH` read-only fields (`id`, `scope_type`, `scope_id`, `created_at`, `last_fired_at`, `last_run_at`, `last_run_status`, `last_run_output`) are silently ignored or rejected with 422 | automated | P1 |
| CRUD-10 | `DELETE` returns 204 and subsequent `GET` returns 404 | automated | P0 |
| CRUD-11 | `GET` on unknown workspace/topic returns 404 | automated | P1 |
| CRUD-12 | `GET`/`PATCH`/`DELETE` on unknown action `id` returns 404 | automated | P1 |
| CRUD-13 | `POST` to workspace-scope action (`scope_type='workspace'`) returns 422 (not implemented in v1) | automated | P1 |
| CRUD-14 | `last_run_at`, `last_run_status`, `last_run_output` are null in freshly created row | automated | P1 |
| CRUD-15 | Concurrent `PATCH` on the same row from two requests — last writer wins, no crash | automated | P2 |

### Area 2: Validation (API-level and DB CHECK)

| ID | Case | Kind | Priority |
|---|---|---|---|
| VAL-01 | `topic_scheduler` missing `cron_expr` → 422 | automated | P0 |
| VAL-02 | `topic_scheduler` with `timing` set → 422 | automated | P0 |
| VAL-03 | `topic_message_sent` missing `timing` → 422 | automated | P0 |
| VAL-04 | `topic_message_sent` with `cron_expr` set → 422 | automated | P0 |
| VAL-05 | `topic_message_sent` with `timing='before'` → 201 (valid) | automated | P0 |
| VAL-06 | `topic_message_sent` with `timing='after'` → 201 (valid) | automated | P0 |
| VAL-07 | `topic_message_received` with `timing='before'` → 422 | automated | P0 |
| VAL-08 | `topic_message_received` with `timing='after'` → 201 (valid per design) | automated | P0 |
| VAL-09 | `topic_archived` with `timing='before'` → 422 | automated | P0 |
| VAL-10 | `topic_archived` with `cron_expr` set → 422 | automated | P0 |
| VAL-11 | Invalid `cron_expr` string (`not-a-cron`) → 422 | automated | P0 |
| VAL-12 | `cron_expr` with sub-minute resolution (e.g. `*/30 * * * * *` — 6-field) → 422 | automated | P1 |
| VAL-13 | Valid five-field `cron_expr='0 9 * * *'` → 201 | automated | P0 |
| VAL-14 | `cron_expr='* * * * *'` (every minute) → 201 | automated | P0 |
| VAL-15 | `staff_name` not validated at write time — referencing a non-existent staff name → 201 (resolution is deferred to fire time) | automated | P1 |
| VAL-16 | `prompt_template` with `{unknown_var}` placeholder → 201 (no app-level validation on template content) | automated | P1 |
| VAL-17 | `event_type` value not in the allowed set → 422 | automated | P0 |
| VAL-18 | DB CHECK constraint enforces correct field/event-type combinations when bypassing the API (direct SQL insert with wrong combo) → SQLite raises `IntegrityError` | automated | P1 |

### Area 3: Variable Substitution

Tests for `render_template` in isolation (pure-Python unit tests, no DB).

| ID | Case | Kind | Priority |
|---|---|---|---|
| TMPL-01 | Known variable `{topic_name}` is substituted correctly | automated | P0 |
| TMPL-02 | All variables for `topic_message_sent` (`{msgbody}`, `{topic_name}`) are substituted | automated | P0 |
| TMPL-03 | All variables for `topic_scheduler` (`{topic_name}`, `{workspace_name}`) are substituted | automated | P0 |
| TMPL-04 | All variables for `topic_archived` (`{topic_name}`) are substituted | automated | P0 |
| TMPL-05 | Unknown placeholder `{no_such_var}` is left as literal `{no_such_var}` (not raised, not dropped) | automated | P0 |
| TMPL-06 | Unknown placeholder triggers a `WARNING` log line | automated | P1 |
| TMPL-07 | `{{` in template produces a literal `{` (standard Python `format_map` escape) | automated | P1 |
| TMPL-08 | `}}` in template produces a literal `}` | automated | P2 |
| TMPL-09 | Template with no placeholders is returned unchanged | automated | P1 |
| TMPL-10 | Empty template string returns empty string | automated | P2 |

### Area 4: Dispatch Core (dispatch_to_staff extraction)

Tests confirming that extracting `dispatch_to_staff` from `send_message` did not
change observable behaviour on the user-message path.

| ID | Case | Kind | Priority |
|---|---|---|---|
| DISP-01 | User sends a message → `dispatch_to_staff` is called with `sender='user'` and the message is inserted into the `messages` table with `sender='user'` | automated | P0 |
| DISP-02 | `dispatch_to_staff` returns the new `message_id` (non-empty string) | automated | P0 |
| DISP-03 | `dispatch_to_staff` publishes to MQTT with the correct topic pattern | automated | P0 |
| DISP-04 | `dispatch_to_staff` called with `sender='event'` inserts a message with `sender='event'` | automated | P0 |
| DISP-05 | Session UUID returned by `dispatch_to_staff` matches `_make_session_uuid` for the given `session_scope` | automated | P1 |

### Area 5: Event Emission and Worker

| ID | Case | Kind | Priority |
|---|---|---|---|
| EMIT-01 | `emit_event` called from the asyncio loop thread enqueues to `app_state.event_queue` (no deadlock, returns immediately) | automated | P0 |
| EMIT-02 | `emit_event` called from a non-loop thread (simulated MQTT/scheduler thread) uses `call_soon_threadsafe` and the event appears in the queue | automated | P0 |
| EMIT-03 | Worker picks up an enqueued event and calls `_dispatch_one` for each matching enabled action | automated | P0 |
| EMIT-04 | Worker handles events in FIFO order (emit A then B; A handled before B) | automated | P1 |
| EMIT-05 | Worker processes one event at a time (no concurrent `_handle_event` calls) | automated | P1 |
| EMIT-06 | Worker updates `app_state.event_worker_last_progress` after successfully handling each event | automated | P1 |
| EMIT-07 | Disabled actions (`enabled=0`) are not dispatched (MQTT publish not called) | automated | P0 |
| EMIT-08 | Actions on a different topic do not fire for an event targeting another topic | automated | P0 |
| EMIT-09 | Worker error isolation: one event whose `_handle_event` raises is logged as `event_worker.handle_failed` and the worker continues processing the next event | automated | P0 |
| EMIT-10 | Worker shutdown: pending events in queue at task cancellation are dropped without raising unhandled exceptions | automated | P1 |

### Area 6: Multi-Action Fanout

| ID | Case | Kind | Priority |
|---|---|---|---|
| FANOUT-01 | Two enabled actions on the same event both fire; MQTT publish is called twice | automated | P0 |
| FANOUT-02 | Failure of one action (renders to `render_error`) does not block the other action from dispatching | automated | P0 |
| FANOUT-03 | Parallel fanout latency: two enabled actions with 5 s artificial delay each complete in ~5 s wall-clock, not ~10 s (gather is concurrent) | automated | P1 |
| FANOUT-04 | Per-dispatch 10 s timeout: a dispatch that hangs is abandoned at ~10 s; `last_run_status='dispatch_error'` is written; sibling in the same event is unaffected and dispatches successfully | automated | P0 |
| FANOUT-05 | After a 10 s timeout on an action, the worker picks up the next queued event promptly (worker is not stuck) | automated | P0 |
| FANOUT-06 | `asyncio.gather(return_exceptions=True)` is used — confirm exceptions from `_dispatch_one` are caught by gather and do not propagate to the worker outer loop | automated | P1 |

### Area 7: Loop Prevention

| ID | Case | Kind | Priority |
|---|---|---|---|
| LOOP-01 | A user message (`sender='user'`) triggers `topic_message_sent`; the resulting event-triggered message has `sender='event'` and does NOT trigger `topic_message_sent` again | automated | P0 |
| LOOP-02 | An agent reply from `_save_agent_response` has `sender='agent'` and triggers `topic_message_received`; an event-triggered dispatch with `sender='event'` does NOT trigger `topic_message_sent` | automated | P0 |
| LOOP-03 | `topic_archived` hook fires regardless of sender (no message involved); no re-triggering concern | automated | P1 |
| LOOP-04 | `topic_scheduler` hook fires regardless of sender; no re-triggering concern | automated | P1 |

### Area 8: Per-Action Observability

| ID | Case | Kind | Priority |
|---|---|---|---|
| OBS-01 | Successful dispatch: `last_run_status='ok'`; `last_run_output` contains `message_id=` | automated | P0 |
| OBS-02 | Missing staff: `last_run_status='staff_missing'`; `last_run_output` contains `staff_name=`; no exception propagates | automated | P0 |
| OBS-03 | Render error (template raises): `last_run_status='render_error'`; `last_run_output` contains error text | automated | P0 |
| OBS-04 | Dispatch timeout: `last_run_status='dispatch_error'`; `last_run_output` contains `timeout after 10s` | automated | P0 |
| OBS-05 | Dispatch exception (not timeout): `last_run_status='dispatch_error'`; `last_run_output` contains error text | automated | P0 |
| OBS-06 | `last_run_at` is updated in all four outcome cases (OBS-01 through OBS-05) and is a valid UTC ISO-8601 string | automated | P0 |
| OBS-07 | `last_run_at` vs `last_fired_at` independence: for a non-scheduler action, `last_fired_at` remains NULL while `last_run_at` is set by the worker | automated | P1 |
| OBS-08 | For a scheduler action, `last_fired_at` is advanced by the tick BEFORE the worker runs, and `last_run_at` is set by the worker AFTER | automated | P1 |
| OBS-09 | `last_run_output` is truncated to 4096 chars (per `_record_run` implementation) | automated | P2 |

### Area 9: Scheduler

| ID | Case | Kind | Priority |
|---|---|---|---|
| SCHED-01 | Cron TZ evaluation: `system.timezone='Asia/Shanghai'`, `cron_expr='0 9 * * *'`, `last_fired_at` at 00:50 UTC — `next_fire` is 01:00 UTC (09:00 Shanghai), not 09:00 UTC | automated | P0 |
| SCHED-02 | Action is due: `last_fired_at` 65 s ago, `cron_expr='* * * * *'` — action fires once | automated | P0 |
| SCHED-03 | Action is not due: `last_fired_at` 5 s ago, `cron_expr='* * * * *'` — action does not fire | automated | P0 |
| SCHED-04 | Archived topic: scheduler action for archived topic does not fire even when cron matches | automated | P0 |
| SCHED-05 | Watermark advances to `next_fire` (not `now`) — drift prevention; watermark is advanced BEFORE enqueue | automated | P0 |
| SCHED-06 | Slow worker does not cause duplicate fire: `last_fired_at` already advanced, next tick computes the NEXT slot (not the same slot again) | automated | P1 |
| SCHED-07 | `last_fired_at` is stored as UTC ISO-8601 with `Z` suffix and round-trips correctly through the scheduler tick | automated | P1 |
| SCHED-08 | New action with `last_fired_at IS NULL` uses `created_at` as the anchor | automated | P1 |
| SCHED-09 | Catch-up policy: after a 5-minute pause with `cron_expr='* * * * *'`, the tick fires the action ONCE (not 5 times) — advancing watermark by one slot per tick | automated | P1 |
| SCHED-10 | Scheduler tick uses the configured TZ from `system.timezone`; changing the setting changes the computed `next_fire` | automated | P1 |
| SCHED-11 | `scheduler_slot` and `scheduler_action_id` are present in the enqueued event dict | automated | P2 |

### Area 10: Stall Watchdog

| ID | Case | Kind | Priority |
|---|---|---|---|
| WATCH-01 | Watchdog positive: worker artificially blocked, queue non-empty, `WARN` log `event_worker.stalled` appears within ~30 s containing queue size | automated | P1 |
| WATCH-02 | Watchdog negative: queue empty for several minutes, no `event_worker.stalled` log | automated | P1 |
| WATCH-03 | Watchdog never cancels the worker (worker task is still running after watchdog fires) | automated | P1 |

### Area 11: Session Sharing

| ID | Case | Kind | Priority |
|---|---|---|---|
| SESS-01 | `@reviewer` invoked via user message uses session key `(topic, topic_id, 'reviewer')`; same staff invoked via `topic_scheduler` event lands on the same `staff_sessions` row and uses `--resume` | automated | P0 |
| SESS-02 | Two different staffs on the same event use different `staff_sessions` rows | automated | P1 |
| SESS-03 | Staff with `session_scope='workspace'` shares session across two topics, both for user-triggered and event-triggered dispatches | automated | P1 |

### Area 12: Frontend UAT

| ID | Case | Kind | Priority |
|---|---|---|---|
| FE-01 | Navigate to topic settings via gear icon in `TopicChat.vue` header; settings page loads at correct route `/workspaces/:wsId/topics/:topicId/settings` | needs-human | P0 |
| FE-02 | Navigate to topic settings via gear icon in `WorkspaceDetail.vue` topic row | needs-human | P0 |
| FE-03 | Event-actions card renders with "Add action" button on empty state | needs-human | P1 |
| FE-04 | Create a `topic_scheduler` action via the form; cron input shows configured TZ (e.g. `Asia/Shanghai`) next to the field | needs-human | P0 |
| FE-05 | Create a `topic_message_sent` action with `timing='before'`; confirm it appears in the list with a rendered prompt snippet | needs-human | P1 |
| FE-06 | Toggle enable/disable via the UI toggle; row reflects the updated state immediately | needs-human | P1 |
| FE-07 | After a dispatch fires, `last_run_at` shows as relative time ("2 min ago") and `last_run_status` shows as a coloured badge | needs-human | P1 |
| FE-08 | `last_run_output` is shown truncated at 120 chars with an expand toggle | needs-human | P1 |
| FE-09 | `dispatch_error` status shows a red badge; `ok` shows green | needs-human | P1 |
| FE-10 | Delete an action via the remove button; action disappears from the list | needs-human | P1 |
| FE-11 | `cron_expr='*/2 * * * *'` action fires 2–3 times over 5 minutes (wall-clock timing) | needs-human | P2 |
| FE-12 | Configure a `topic_archived` action with `@summariser`; archive the topic; a summary message lands in the topic before the topic is hidden from the active list | needs-human | P0 |
| FE-13 | Set `system.timezone='Asia/Shanghai'` in settings; configure `cron_expr='0 9 * * *'`; verify UI displays "fires daily at 09:00 in Asia/Shanghai" next to the cron input | needs-human | P1 |
| FE-14 | Variable help text is shown next to the prompt template input and lists the correct variables for the selected `event_type` | needs-human | P2 |
| FE-15 | Form validation prevents submitting a `topic_scheduler` action without `cron_expr` | needs-human | P1 |

---

## Edge Cases and Additional Coverage

The following cases go beyond the "Test plan key cases" list in the design doc:

- **VAL-12**: Six-field cron (with seconds) must be rejected. The design says sub-minute is rejected at API write time; the validator should handle the six-field variant.
- **VAL-18**: Bypassing the API and inserting a row directly into SQLite must still be caught by DB CHECK. This exercises defence-in-depth.
- **CRUD-09**: Read-only field handling on PATCH is under-specified in §7 ("silently ignored or rejected with 422"). The test is parametrized against both outcomes so the engineer can confirm which is implemented.
- **CRUD-15**: Concurrent PATCH — not explicitly called out in the design doc, but important for the self-hosted single-user case where rapid UI interactions can race.
- **EMIT-10**: Worker shutdown loss — explicitly called out in the ADR consequences. Test confirms the documented behaviour (loss on shutdown) and that no unhandled exception propagates.
- **SCHED-09**: Catch-up policy fires once per tick, not once per missed minute. This is an easy mistake to make; the test locks it.
- **OBS-09**: The `_record_run` function truncates `last_run_output` at 4096 chars. Not mentioned in the design doc; spotted in the pseudocode in §4.
- **FANOUT-03**: Parallel fanout latency. The design explicitly calls this a test case; it exercises that `asyncio.gather` is actually parallel, not sequential.
- **WATCH-02**: Watchdog negative — this prevents the watchdog from producing false-positive alerts in idle deployments. Important for operator experience.

---

## Ambiguities Flagged for Engineer

The following points in the design doc are unclear from a test-author perspective.
Each one may require clarification before the corresponding test body can be written.

1. **CRUD-09 — PATCH semantics for read-only fields.** §7 says `id`, `scope_type`,
   `scope_id`, `created_at`, `last_fired_at`, `last_run_at`, `last_run_status`,
   `last_run_output` are "read-only", but does not specify whether a PATCH
   containing them returns 422 or silently ignores them. Pydantic model shape
   determines this. Clarify so the test can assert the right status code.

2. **VAL-08 — `topic_message_received` with `timing='after'`.** The DB CHECK
   allows `timing IS NULL OR timing = 'after'` for `topic_message_received`.
   The API validation section (§7) says "timing null or `'after'`". The design
   simultaneously says in §5 that `topic_message_received` fires "after only".
   So `timing='after'` is technically valid but redundant. Test VAL-08 treats
   it as a 201; confirm this is correct.

3. **VAL-12 — six-field cron rejection.** The ADR says "sub-minute cron
   expressions are rejected at API write time", and `croniter.is_valid` accepts
   six-field expressions by default. Confirm whether the validator calls
   `croniter.is_valid(expr)` or `croniter.is_valid(expr, hash_values=False)` or
   does a field-count check before calling `croniter`. This determines whether
   `*/30 * * * * *` returns a 422 or a 201.

4. **DISP-04 — `sender='event'` in the messages table.** The design adds a third
   `sender` value but the existing `messages` table schema is not shown in the
   design doc. Confirm whether there is a CHECK constraint on `messages.sender`
   that needs to be updated, or whether the column is unconstrained.

5. **FANOUT-03 — wall-clock timing in unit tests.** Timing assertions against
   real asyncio delays are inherently flaky in CI (especially under load).
   Confirm the preferred approach: use `asyncio.sleep` mocking, `freezegun`, or
   accept a loose bound (e.g. assert wall time < 8 s for two 5 s delays). The
   scaffolding leaves a `# TODO` here.

6. **WATCH-01 — watchdog poll cadence in tests.** The watchdog sleeps 30 s
   between polls. In automated tests this would make WATCH-01 extremely slow.
   Confirm whether the watchdog's sleep interval is configurable (e.g. via an
   injectable constant or a monkeypatched `asyncio.sleep`) to allow a fast-poll
   variant in tests.

---

## Non-Functional Requirements

| Requirement | Verification approach |
|---|---|
| `emit_event` must return immediately from any thread context | Assert wall time < 10 ms (monkeypatched queue) |
| Worker block per event is bounded at ~10 s regardless of N matching actions | FANOUT-04 / FANOUT-05 |
| No duplicate fires from slow worker | SCHED-06 |
| Disabled action produces no MQTT publish | EMIT-07 |
| Stall watchdog does not false-positive on idle queue | WATCH-02 |
| Worker shutdown does not raise unhandled exceptions | EMIT-10 |

---

## Out of Scope for This Test Plan

- Stack tests against a live MQTT broker (deferred to integration phase).
- UAT execution against a dev/staging env (deferred to step 15 of the workflow).
- Reply-tier status on the action card (deferred enhancement, not in v1).
- Cross-cutting TZ-awareness audit of existing date columns (tracked in issue #158).
- Workspace-scope events (not implemented in v1; API returns 422 — covered by CRUD-13).
