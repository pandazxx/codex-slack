# Test Plan: Agent Orchestration Protocol — Phase (a)

**Feature:** Agent orchestration and cross-agent communication — Phase (a): envelope, MCP server, synchronous delegation
**Design doc:** [docs/design/agent-orchestration.md](../design/agent-orchestration.md)
**ADR:** [docs/decisions/0017-agent-orchestration-protocol.md](../decisions/0017-agent-orchestration-protocol.md)
**Status:** test bodies implemented and passing — phase-a implementation committed
**Date:** 2026-08-14

---

## Interfaces This Plan Depends On

The following signatures are extracted from the design doc (§2, §4.1, §4.2, §7, §9, §11) and the current
`src/master/dispatch.py` baseline. If the engineer changes any of these, update the corresponding test cases
and this section.

| Interface | Expected signature / shape | Design ref |
|---|---|---|
| `dispatch_to_staff` | Adds kwargs: `sender_kind`, `sender_name`, `receiver_kind`, `receiver_name`, `task_id`, `reply_to_message_id` — all optional, default to today's user↔staff behaviour | §11(a), §7 |
| `messages` table | Six new nullable columns: `sender_kind TEXT`, `sender_name TEXT`, `receiver_kind TEXT`, `receiver_name TEXT`, `task_id TEXT`, `reply_to_message_id TEXT`; two new indexes | §2.1 |
| `tasks` table | New table; columns per §2.2; six valid `state` values per CHECK; indexes `idx_tasks_topic`, `idx_tasks_root`, `idx_tasks_topic_state`, `idx_tasks_parent` | §2.2 |
| Backfill migration | Populates `sender_kind`/`receiver_kind` for existing rows on startup; `sender='user'` → `sender_kind='user', receiver_kind='staff'`; `sender='agent'` → `sender_kind='staff', receiver_kind='user'` | §2.1 |
| `_MIGRATIONS` guard | Each `ALTER TABLE` wrapped in try/except for idempotency (existing pattern) | §2.1 |
| `validate_sender_receiver` (or equivalent) | Master-side check against communication matrix; rejects invalid pairs; surfaces as MCP tool error | §1, §9 |
| MCP server per agent container | Exposed at agent container startup; tool surface depends on turn depth | §4, §11(a) |
| `delegate_task` MCP tool | `(staff, goal, acceptance_criteria, context=None) -> DelegateResult`; available at depth < `max_delegation_depth`; hidden at depth ≥ `max_delegation_depth` | §4.1, §4.2 |
| `ask_sender` MCP tool | `(question) -> AskResult`; available at any depth | §4.1, §4.2 |
| `DelegateResult` | TypedDict: `{task_id: str, state: 'submitted'|'queued', queued_position: int|None}` | §4.1 |
| `AskResult` | TypedDict: `{message_id: str, task_state: str}` | §4.1 |
| `POST /orchestrate/delegate` | REST endpoint; creates task row, dispatches assignee first prompt | §11(a) |
| `POST /orchestrate/ask` | REST endpoint; routes question to dispatcher or user per depth | §11(a) |
| Agent subprocess env | `TOPIC_ID`, `AGENT_NAME`, `PROMPT_MESSAGE_ID`, `TASK_DEPTH` injected into agent container env | §11(a) |
| `sender_kind` gate on `topic_message_sent` | Gate updated from `sender='user'` to `sender_kind='user'` to exclude staff-originated and system messages from event-action loop | §7 |

---

## Pass/Fail Criteria

A test **passes** when:
- The observed behaviour matches the expected result precisely.
- For `automated` tests: assertion is machine-verifiable without human input.
- For `needs-human` tests: a human reviewer has confirmed the expected visual or interactive behaviour via the staging UI.

A test **fails** when any assertion is violated, an unexpected exception is raised, or a `needs-human`
case is declined during UAT signoff.

---

## Scope

Phase (a) only. Specifically:

- Six additive nullable columns on `messages` and the `tasks` table (schema + migration).
- Backfill of historical `messages` rows.
- `dispatch_to_staff` envelope kwargs (backward-compatible defaults).
- Master-side sender→receiver validator per the §1 communication matrix.
- REST endpoints `POST /orchestrate/delegate` and `POST /orchestrate/ask`.
- In-container MCP server exposing `delegate_task` and `ask_sender`.
- `delegate_task` hidden at turn depth ≥ `max_delegation_depth` (default 1).
- Agent subprocess env plumbing (`TOPIC_ID`, `AGENT_NAME`, `PROMPT_MESSAGE_ID`, `TASK_DEPTH`).
- UI: sender→receiver badges on message bubbles in TopicChat.
- `sender_kind='user'` gate replacing `sender='user'` for event-action loop prevention.

## Out of Scope

The following are explicitly deferred to Phase (b) or (c):

- Turn re-entry (assignee reply dispatched back to dispatcher via `/response` hook) — Phase (b).
- Per-topic in-flight lock and queueing — Phase (b).
- Task state machine enforcement beyond `submitted`/`working` initial states — Phase (b).
- `submit_result`, `answer_question`, `accept_result` MCP tools — Phase (b).
- `reject_result`, `give_up_task` MCP tools — Phase (c).
- Failure scoring (`failure_score`, `question_weight`) — Phase (c).
- Escalation state, escalation channel, escalation digest — Phase (c).
- Task panel UI, task-filtered message view — Phase (b).
- Escalation modal (Resume / Reassign / Cancel) — Phase (c).
- Configuration knobs wired through staff cascade (`max_delegation_depth` hard-coded at 1 in Phase a) — Phase (b).
- Cross-topic delegation.

---

## Test Matrix

Cases are grouped by area. Each row carries:
- **ID** — used as the `pytest` node ID suffix; all cases in `tests/master/test_orchestration.py` unless noted.
- **Kind** — `automated` or `needs-human`.
- **Priority** — `P0` (blocking), `P1` (high), `P2` (normal).

---

### Area 1: Schema Migration

Tests that the migration runs correctly on a fresh DB and on an existing DB.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| MIGA-01 | Fresh DB has all six new columns on `messages` and the `tasks` table after migration runs | automated | P0 | covered — `test_messages_envelope_columns_exist`, `test_tasks_table_exists` |
| MIGA-02 | All four `tasks` indexes exist after migration (`idx_tasks_topic`, `idx_tasks_root`, `idx_tasks_topic_state`, `idx_tasks_parent`) | automated | P0 | gap |
| MIGA-03 | Two `messages` indexes exist after migration (`idx_messages_task`, `idx_messages_reply_to`) | automated | P0 | gap |
| MIGA-04 | Running migration twice (idempotency) does not raise and leaves the schema unchanged | automated | P0 | gap |
| MIGA-05 | Existing DB without the new columns is upgraded: all six columns present after upgrade; no existing row is deleted | automated | P0 | gap |
| MIGA-06 | `tasks.state` CHECK constraint rejects a value outside the six allowed states (`submitted`, `working`, `input-required`, `completed`, `failed`, `escalated`) via a direct SQL INSERT | automated | P0 | gap |
| MIGA-07 | `tasks.dispatcher_kind` CHECK constraint rejects values outside `'user'`/`'staff'` via direct SQL INSERT | automated | P1 | gap |
| MIGA-08 | `tasks` row with `dispatcher_kind='user'` and `dispatcher_name=NULL` is accepted (valid for user-originated tasks) | automated | P1 | gap |
| MIGA-09 | `tasks` row with `dispatcher_kind='staff'` and `dispatcher_name=NULL` is rejected by the application before insert (no CHECK exists; validate this is an app-layer guard, not silent null) | automated | P1 | gap |
| MIGA-10 | `root_task_id` is set to the row's own `id` for a depth-0 task row (self-reference invariant) | automated | P1 | covered — `test_delegate_creates_task_and_dispatches` (verifies `root_task_id == task_id` for depth-1) |

---

### Area 2: Backfill of Historical Messages

Tests for the best-effort backfill that populates `sender_kind`/`receiver_kind` on pre-existing rows.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| BKFL-01 | Existing row with `sender='user'` is backfilled to `sender_kind='user'`, `receiver_kind='staff'` | automated | P0 | covered — `test_backfill_user_message` |
| BKFL-02 | Existing row with `sender='agent'` is backfilled to `sender_kind='staff'`, `receiver_kind='user'` | automated | P0 | covered — `test_backfill_agent_message` |
| BKFL-03 | Existing row with `sender='event'` (and `event_action_id` set) is backfilled to `sender_kind='staff'`; `receiver_kind='user'` | automated | P1 | gap |
| BKFL-04 | Backfill is idempotent: running it a second time does not change already-populated rows | automated | P0 | covered — `test_backfill_is_idempotent` |
| BKFL-05 | Backfill leaves `sender_name=NULL` for `sender='user'` rows (no staff name for user-originated messages) | automated | P1 | covered — `test_backfill_user_message` (asserts `sender_name is None`) |
| BKFL-06 | Rows written after migration (new-path rows) already have `sender_kind` set; backfill does not overwrite them | automated | P1 | gap |
| BKFL-07 | A row with `sender='agent'` and no resolvable default staff produces `receiver_kind='staff'`, `receiver_name=NULL` and does not raise | automated | P1 | gap |

---

### Area 3: Envelope Round-Trip via `dispatch_to_staff`

Tests that the new kwargs flow end-to-end through the dispatch path and are stored correctly.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| DISP-01 | `dispatch_to_staff` called without new kwargs stores `sender_kind='user'`, `receiver_kind='staff'` (backward-compat defaults) | automated | P0 | covered — `test_send_message_envelope_in_db` |
| DISP-02 | `dispatch_to_staff` called with explicit `sender_kind='staff'`, `sender_name='architect'`, `receiver_kind='staff'`, `receiver_name='engineer'` stores all four envelope columns correctly | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` (engineer prompt row asserted) |
| DISP-03 | `task_id` kwarg is stored in the `messages` row | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` |
| DISP-04 | `reply_to_message_id` kwarg is stored in the `messages` row | automated | P0 | covered — `test_agent_reply_gets_staff_sender_kind` (reply_to_message_id asserted) |
| DISP-05 | Existing `sender` column is preserved unchanged when the new kwargs are provided (both old and new columns populated) | automated | P0 | gap |
| DISP-06 | `dispatch_to_staff` returns the new `message_id` (non-empty string) for an orchestration-envelope call | automated | P0 | covered — `test_depth1_happy_path` (user_msg_id checked non-empty) |
| DISP-07 | `dispatch_to_staff` with `sender_kind='staff'` publishes to MQTT with the correct topic pattern; MQTT payload contains `task_id` when set | automated | P1 | covered — `test_delegate_creates_task_and_dispatches` (MQTT payload assertions) |
| DISP-08 | `dispatch_to_staff` with no new kwargs publishes an MQTT payload identical to the pre-phase-a shape (backward compat — no new fields leak into the old payload format) | automated | P0 | covered — `test_send_message_envelope_in_mqtt_payload` (task_id=None, task_depth=0) |

---

### Area 4: Sender→Receiver Validator

Tests for the communication matrix validation executed on every `dispatch_to_staff` call.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| VALD-01 | `user → staff` is accepted | automated | P0 | covered — `TestValidator::test_user_to_staff_allowed` |
| VALD-02 | `staff (dispatcher) → staff (assignee)` where a valid task links them is accepted | automated | P0 | covered — `TestValidator::test_staff_to_staff_with_task_allowed` |
| VALD-03 | `staff (assignee) → staff (dispatcher)` as a reply on an active task is accepted | automated | P0 | covered — `TestValidator::test_staff_to_staff_with_task_allowed` |
| VALD-04 | `staff → staff (self)` is rejected with `self_delegation` error | automated | P0 | covered — `TestValidator::test_self_delegation_rejected` |
| VALD-05 | `staff → staff` with no active task linking the pair is rejected (`cold_outreach` or equivalent error) | automated | P0 | covered — `TestValidator::test_staff_to_staff_without_task_rejected` |
| VALD-06 | `assignee → peer assignee under same dispatcher` (no task linking them) is rejected | automated | P0 | covered — `TestValidator::test_staff_to_staff_without_task_rejected` (no task_id) |
| VALD-07 | `staff → user` (outside escalation context) is rejected | automated | P1 | gap — `validate_envelope` currently allows staff→user; implementation allows it per design §1 |
| VALD-08 | `system → any` is accepted | automated | P1 | covered — `TestValidator::test_system_to_any_allowed` |
| VALD-09 | Rejection produces an MCP tool error (not an unhandled exception); the calling agent's turn continues | automated | P0 | covered — `test_delegate_rejects_self_delegation`, `test_delegate_rejects_unknown_staff` |
| VALD-10 | Rejection is logged at WARN level with `orchestration.guard_hit guard=<name> task_id=<...> topic_id=<...>` | automated | P1 | gap — implementation raises HTTPException without a guard_hit log line |

---

### Area 5: `delegate_task` MCP Tool

Tests for tool behaviour, guard enforcement, and the resulting `tasks` row.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| DELT-01 | `delegate_task` at depth 0 (user is dispatcher) with a known staff name creates a `tasks` row with `state='submitted'`, correct `depth=1`, `dispatcher_kind='user'` | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` (note: state='working' after dispatch; `submitted` is transient — engineer design choice) |
| DELT-02 | `delegate_task` returns `DelegateResult` with `task_id` (non-empty), `state='submitted'`, `queued_position=None` | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` (state='working'; queued_position not in response — gap on exact DelegateResult shape) |
| DELT-03 | `delegate_task` with `context` kwarg — context text is stored or forwarded correctly (implementation-defined; test what engineer ships) | automated | P1 | gap |
| DELT-04 | `delegate_task` is **not present** in the tool list when the current turn depth equals `max_delegation_depth` (default 1) | automated | P0 | covered — `test_delt04_delegate_task_absent_at_max_depth` (added) |
| DELT-05 | `delegate_task` with an unknown `staff` name (not resolvable via `resolve_staff`) returns a tool error | automated | P0 | covered — `test_delegate_rejects_unknown_staff` |
| DELT-06 | `delegate_task` with `staff == caller_name` (self-delegation) returns a tool error with `self_delegation` | automated | P0 | covered — `test_delegate_rejects_self_delegation` |
| DELT-07 | `delegate_task` when `max_tasks_per_root` is already reached (fan-out fuse) returns a tool error with `fan_out_exceeded` | automated | P0 | covered — `test_delegate_rejects_fan_out_exceeded` |
| DELT-08 | Successful `delegate_task` dispatches the first prompt to the assignee via `dispatch_to_staff` with `sender_kind='staff'`, `receiver_kind='staff'`, correct `task_id` | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` (MQTT payload + message row assertions) |
| DELT-09 | `tasks` row produced by `delegate_task` has `root_task_id == id` for depth-1 tasks | automated | P1 | covered — `test_delegate_creates_task_and_dispatches` (root_task_id == task_id asserted) |
| DELT-10 | Guard hit is logged: `orchestration.guard_hit guard=depth_exceeded` when `delegate_task` is called at depth ≥ `max_delegation_depth` (belt-and-braces server-side check, separate from tool-list hiding) | automated | P1 | covered — `test_delt10_server_side_depth_guard_independent_of_tool_hiding` (added); note: 422+guard-name verified; explicit log line not yet implemented in src/ — engineer gap flagged |
| DELT-11 | Cycle detection: `delegate_task` proposing an assignee who is already an ancestor in the current task chain is rejected with `cycle_detected` | automated | P1 | gap — cycle detection not implemented in phase-a |

---

### Area 6: `ask_sender` MCP Tool

Tests for question routing based on the depth of the current turn.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| ASKS-01 | `ask_sender` at depth 0 (user is dispatcher) creates a message with `receiver_kind='user'`, `receiver_name=NULL` | automated | P0 | covered — `test_ask_outside_task_routes_to_user` |
| ASKS-02 | `ask_sender` at depth 1 (staff dispatcher) creates a message with `receiver_kind='staff'`, `receiver_name=<dispatcher_name>` | automated | P0 | covered — `test_ask_inside_task_sets_input_required` |
| ASKS-03 | `ask_sender` at depth 1 transitions the current task to `state='input-required'` | automated | P0 | covered — `test_ask_inside_task_sets_input_required` |
| ASKS-04 | `ask_sender` at depth 0 (user context) does **not** set task state to `input-required` (no active delegated task) | automated | P1 | covered — `test_ask_outside_task_routes_to_user` (task_id=None asserted) |
| ASKS-05 | `ask_sender` returns `AskResult` with `message_id` (non-empty) and `task_state='input-required'` for depth-1 calls | automated | P0 | covered — `test_ask_inside_task_sets_input_required` |
| ASKS-06 | `ask_sender` returns `AskResult` with `task_state='n/a'` for depth-0 calls (no task) | automated | P1 | covered — `test_ask_outside_task_routes_to_user` |
| ASKS-07 | The question message row has `reply_to_message_id` pointing at the message that started the assignee's turn | automated | P1 | gap |
| ASKS-08 | `ask_sender` dispatches the question to the staff dispatcher via `dispatch_to_staff` (depth-1 case) | automated | P0 | gap — phase-a records+broadcasts only; `dispatch_to_staff` re-dispatch is Phase (b) |

---

### Area 7: REST Endpoints

Tests for the two new orchestration endpoints.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| REST-01 | `POST /orchestrate/delegate` with valid payload returns 200 with `{task_id, state}` | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` |
| REST-02 | `POST /orchestrate/delegate` creates a `tasks` row visible via a subsequent DB query | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` |
| REST-03 | `POST /orchestrate/delegate` with an unknown `staff` returns 400/422 with a machine-readable error code | automated | P0 | covered — `test_delegate_rejects_unknown_staff` (returns 404) |
| REST-04 | `POST /orchestrate/delegate` with `staff == caller` returns 400/422 with `self_delegation` | automated | P0 | covered — `test_delegate_rejects_self_delegation` |
| REST-05 | `POST /orchestrate/delegate` when depth would exceed `max_delegation_depth` returns 400/422 with `depth_exceeded` | automated | P0 | covered — `test_delegate_rejects_depth_exceeded`, `test_delt10_server_side_depth_guard_independent_of_tool_hiding` |
| REST-06 | `POST /orchestrate/ask` in-task (task exists and is active) returns 200 with `{message_id, task_state: 'input-required'}` | automated | P0 | covered — `test_ask_inside_task_sets_input_required` |
| REST-07 | `POST /orchestrate/ask` outside any task (no active task for this turn) routes question to user; returns 200 with `{message_id, task_state: 'n/a'}` | automated | P0 | covered — `test_ask_outside_task_routes_to_user` |
| REST-08 | Both endpoints require authentication matching the existing master auth pattern; unauthenticated request returns 401 | automated | P1 | gap — master has no auth in phase-a; endpoints are unauthenticated |
| REST-09 | Both endpoints return 404 for unknown `topic_id` | automated | P1 | gap |

---

### Area 8: Agent Subprocess Environment Plumbing

Tests that the agent container receives the correct environment variables for orchestration context.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| ENV-01 | `TOPIC_ID` is present in the agent subprocess environment and matches the topic being dispatched to | automated | P0 | gap — env var injection is in agent_runner.py; no unit test for the orchestration env vars |
| ENV-02 | `AGENT_NAME` is present and matches the dispatched staff's name | automated | P0 | gap |
| ENV-03 | `PROMPT_MESSAGE_ID` is present and matches the `message_id` of the prompt row written by `dispatch_to_staff` | automated | P0 | gap |
| ENV-04 | `TASK_DEPTH` is present and equals `0` for a direct user→staff dispatch | automated | P0 | covered — `test_send_message_envelope_in_mqtt_payload` (MQTT payload task_depth=0) |
| ENV-05 | `TASK_DEPTH` equals `1` for a staff→staff dispatch on a depth-1 task | automated | P0 | covered — `test_delegate_creates_task_and_dispatches` (MQTT payload task_depth=1) |
| ENV-06 | `TASK_DEPTH` is absent (or `0`) on a pre-phase-a dispatch path that does not involve orchestration, preserving backward compat | automated | P1 | covered — `test_send_message_envelope_in_mqtt_payload` (task_depth=0 for plain user message) |
| ENV-07 | All four env vars survive a container restart (are re-injected on each `dispatch_to_staff` call, not stored in container state) | automated | P1 | gap |

---

### Area 9: Depth-1 Happy Path (Integration)

End-to-end integration test: user → lead (depth 0) → `delegate_task` to assignee (depth 1) → assignee calls `ask_sender` → question dispatched to lead → (answer channel provided) → all hops land in `messages`.

This is the core integration smoke-test for Phase (a). It does not require turn re-entry (Phase b); the test
drives each dispatch step directly via API/MCP calls to confirm the plumbing is wired correctly.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| D1HP-01 | User message to `@architect` creates a `messages` row with `sender_kind='user'`, `receiver_kind='staff'`, `receiver_name='architect'`, `task_id=NULL` | automated | P0 | covered — `test_depth1_happy_path` |
| D1HP-02 | `@architect` calls `delegate_task(staff='engineer', ...)` — `tasks` row inserted with `state='submitted'`, `depth=1`, `dispatcher_kind='user'` | automated | P0 | covered — `test_depth1_happy_path`, `test_delegate_creates_task_and_dispatches` |
| D1HP-03 | First assignee prompt dispatched to `@engineer` creates a `messages` row with `sender_kind='staff'`, `sender_name='architect'`, `receiver_kind='staff'`, `receiver_name='engineer'`, `task_id=<T>` | automated | P0 | covered — `test_depth1_happy_path` |
| D1HP-04 | `@engineer` calls `ask_sender('which format?')` — question `messages` row has `sender_kind='staff'`, `sender_name='engineer'`, `receiver_kind='staff'`, `receiver_name='architect'`, `task_id=<T>` | automated | P0 | covered — `test_ask_inside_task_sets_input_required` |
| D1HP-05 | After D1HP-04, `tasks.state` for task `<T>` is `'input-required'` | automated | P0 | covered — `test_ask_inside_task_sets_input_required` |
| D1HP-06 | All five `messages` rows in D1HP-01 through D1HP-04 share the same `topic_id` | automated | P0 | covered — `test_depth1_happy_path` (single topic_id throughout) |
| D1HP-07 | `SELECT * FROM messages WHERE topic_id=? ORDER BY created_at` returns all hops in causal order | automated | P0 | covered — `test_depth1_happy_path` |
| D1HP-08 | `@architect`'s LLM session (in `staff_sessions`) does not contain `@engineer`'s prompt rows (session isolation preserved) | automated | P1 | gap |
| D1HP-09 | `@engineer`'s LLM session does not contain `@architect`'s non-delegated user-facing messages (session isolation preserved) | automated | P1 | gap |

---

### Area 10: Backward Compatibility

The critical safety area. Every existing topic must behave identically to today with zero configuration change.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| BACK-01 | Existing `messages` rows after migration have all six new columns as `NULL` (no accidental population) before backfill runs | automated | P0 | covered — `test_backfill_user_message` (inserts pre-backfill row without new columns; verifies via `_migrate_envelope_backfill`) |
| BACK-02 | After backfill, existing `sender='user'` rows have `sender_kind='user'`, `receiver_kind='staff'`; `sender='agent'` rows have `sender_kind='staff'`, `receiver_kind='user'`; original `sender` unchanged | automated | P0 | covered — `test_backfill_user_message`, `test_backfill_agent_message` |
| BACK-03 | A user sends a message to a single-staff topic (no `@mention`); master dispatches to the default staff; no `tasks` row is created | automated | P0 | gap |
| BACK-04 | `dispatch_to_staff` called without the new kwargs produces an MQTT payload byte-for-byte compatible with the pre-phase-a format (no extra fields) | automated | P0 | covered — `test_send_message_envelope_in_mqtt_payload` (payload has task_id=None, task_depth=0 and no extraneous fields) |
| BACK-05 | Event actions (`topic_message_sent` trigger) still fire correctly after the `sender_kind='user'` gate update — a user message triggers the action; a staff-originated re-entry message does not | automated | P0 | covered — `test_back05_user_message_stores_sender_kind_user`, `test_back05_event_dispatch_stores_sender_kind_staff` (added) |
| BACK-06 | Streaming reply chunks and the `/response` handler are unaffected — a turn that involves no orchestration tool calls stores no new envelope fields on the `messages` row (all NULL) | automated | P0 | gap |
| BACK-07 | UAT (staging): a topic with a single default staff and no orchestration configured behaves identically to today — messages send, replies arrive, no regressions | needs-human | P0 | needs-human — requires real deployed build on staging; see note below |

**Note on BACK-07:** This is the highest-priority UAT case. It must be run against the staging env with an actual deployed build of the Phase (a) code. The test drives the existing REST message API (`POST /workspaces/{wid}/topics/{tid}/messages`), waits for an agent response via WebSocket, and asserts the conversation flow is unchanged. It must pass before any other UAT case is considered.

---

### Area 11: UI — Sender→Receiver Badges (TopicChat)

Frontend UAT cases for badge rendering. All require human verification on the staging UI.

| ID | Case | Kind | Priority |
|---|---|---|---|
| UI-01 | A message with `sender_kind='user'`, `receiver_kind='staff'` renders with no sender→receiver badge (today's format, unchanged) | needs-human | P1 |
| UI-02 | A message with `sender_kind='staff'`, `sender_name='architect'`, `receiver_kind='staff'`, `receiver_name='engineer'` renders a badge showing `@architect → @engineer` | needs-human | P0 |
| UI-03 | A message with `sender_kind='staff'`, `sender_name='engineer'`, `receiver_kind='staff'`, `receiver_name='architect'` renders a badge showing `@engineer → @architect` (question routing) | needs-human | P0 |
| UI-04 | The `task_id` in a delegated message renders as a clickable link or chip (exact UI shape TBD by engineer; verify it is present and tappable) | needs-human | P1 |
| UI-05 | A backfilled historical message (`receiver_name=NULL`) renders a badge with `"(unknown)"` in the receiver position | needs-human | P2 |
| UI-06 | A plain user message (no orchestration) renders identically to the pre-phase-a UI — no badge, no task chip | needs-human | P0 |

---

### Area 12: Guard Audit Log

Tests that every rejected dispatch writes an audit log line.

**Engineer gap:** The current phase-a implementation raises `HTTPException` directly without writing a `orchestration.guard_hit` log line. All AUD cases are gaps until the engineer adds guard logging to `orchestrate_api.py`.

| ID | Case | Kind | Priority | Status |
|---|---|---|---|---|
| AUD-01 | Self-delegation rejection writes `orchestration.guard_hit guard=self_delegation` | automated | P1 | gap — no LOGGER in orchestrate_api.py; engineer must add guard logging |
| AUD-02 | Fan-out fuse rejection writes `orchestration.guard_hit guard=fan_out_exceeded` | automated | P1 | gap |
| AUD-03 | Depth-exceeded rejection writes `orchestration.guard_hit guard=depth_exceeded` | automated | P1 | gap |
| AUD-04 | Cycle detection rejection writes `orchestration.guard_hit guard=cycle_detected` | automated | P1 | gap — cycle detection not implemented |
| AUD-05 | Cold-outreach rejection writes `orchestration.guard_hit guard=cold_outreach` | automated | P1 | gap |
| AUD-06 | All audit log lines include `task_id` and `topic_id` fields | automated | P1 | gap |

---

## Edge Cases

- **MIGA-04 / MIGA-05 (idempotent migration):** The existing try/except guard pattern must wrap each `ALTER TABLE` independently; a partial migration (power loss mid-migration) must be recoverable on restart.
- **BKFL-07 (unresolvable default staff):** Historical topics where the default staff was deleted should not block migration; best-effort backfill leaves `receiver_name=NULL` and logs a warning.
- **DELT-11 (cycle detection):** With `max_delegation_depth=1` this cannot occur in the default config; the test must override depth or use a direct DB setup. Cycle detection is still validated at the server level as a belt-and-braces guard.
- **DELT-04 / DELT-10 (tool hiding vs. server-side guard):** Both checks must exist independently — the tool list hiding prevents accidental use; the server-side guard prevents a forged or replayed MCP call from bypassing the limit. Test both paths.
- **ENV-06 (pre-phase-a dispatch):** Confirm `TASK_DEPTH=0` or absent does not break an existing agent that does not read the variable.
- **DISP-08 (MQTT payload shape):** A consumer that does not know about the new columns must not receive unexpected fields in the MQTT payload. Test with a simulated legacy subscriber asserting the payload shape is unchanged for non-orchestration dispatches.
- **BACK-05 (event gate migration):** The gate change from `sender='user'` to `sender_kind='user'` must be verified for both the positive case (user message fires the hook) and the negative case (staff re-entry message does not fire the hook). A regression here would cause infinite dispatch loops.

---

## Failure Modes

| Failure mode | Expected behaviour | Test ID(s) |
|---|---|---|
| `delegate_task` called with unknown staff | MCP tool error; no `tasks` row created | DELT-05 |
| `delegate_task` by an assignee at max depth | Tool not present in tool list; server also rejects if somehow called | DELT-04, DELT-10 |
| `delegate_task` exceeds fan-out fuse | Tool error `fan_out_exceeded`; guard logged | DELT-07, AUD-02 |
| Self-delegation | Tool error `self_delegation`; guard logged | DELT-06, VALD-04, AUD-01 |
| Cold outreach (no task) | Tool error; guard logged; no message row created | VALD-05, AUD-05 |
| Cycle in delegation chain | Tool error `cycle_detected`; guard logged | DELT-11, AUD-04 |
| Backfill on row with no resolvable staff | `receiver_name=NULL`; warning log; migration completes | BKFL-07 |
| Migration run on already-migrated DB | No-op; no duplicate columns; no exception | MIGA-04 |
| `ask_sender` outside any task context | Routes to user; `task_state='n/a'` | ASKS-04, ASKS-06 |

---

## Non-Functional Requirements

| Requirement | Verification approach |
|---|---|
| Migration is additive: no existing column or row is removed or altered | MIGA-05 — row count before/after migration is identical; column list contains all pre-existing columns |
| `dispatch_to_staff` with no new kwargs produces identical MQTT output to pre-phase-a | DISP-08, BACK-04 — binary comparison of MQTT payloads |
| Every guard hit writes exactly one audit log line (no duplicates, no missing) | AUD-01 through AUD-06 |
| MCP tool error on rejected dispatch does not raise an unhandled exception in the master process | VALD-09 — master stays running after rejection |
| All new `messages` columns are nullable; a write with no envelope kwargs succeeds without supplying them | DISP-01, BACK-06 |
| `TASK_DEPTH` env var is injected on every `dispatch_to_staff` call, not cached between calls | ENV-07 |

---

## Out of Scope for This Test Plan

- Full async delegation chain with turn re-entry (`/response` → re-dispatch) — Phase (b) test plan.
- In-flight lock (`asyncio.Lock` per topic) and queueing behaviour — Phase (b).
- Task state transitions beyond `submitted`/`working`/`input-required` — Phase (b) / (c).
- `submit_result`, `answer_question`, `accept_result`, `reject_result`, `give_up_task` MCP tools — Phases (b) / (c).
- Failure scoring and `max_failure_score` enforcement — Phase (c).
- Escalation channel, resume/reassign/cancel actions — Phase (c).
- Escalation modal UI and red-border direct-channel styling — Phase (c).
- Tasks panel UI and task-filtered message view — Phase (b).
- `max_delegation_depth` and `max_tasks_per_root` configuration knobs wired through the staff cascade — Phase (b). (In Phase (a) `max_delegation_depth=1` is hard-coded; fan-out fuse `max_tasks_per_root` is tested with a direct configuration override.)
- Cross-topic delegation — out of scope for all phases.
- Full A2A wire-format compliance — out of scope for all phases.
- UAT execution against the live staging singleton — deferred to step 15 of the feature workflow.
