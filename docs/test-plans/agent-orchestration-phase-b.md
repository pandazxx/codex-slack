# Test Plan: Agent Orchestration Protocol — Phase (b)

**Feature:** Agent orchestration and cross-agent communication — Phase (b): staged-dispatch model,
turn re-entry, task state machine enforcement, submit/answer/accept MCP tools,
per-topic lock + FIFO queue, config knobs, tasks list endpoint, tasks panel UI.
**Design doc:** [docs/design/agent-orchestration.md](../design/agent-orchestration.md)
**ADR:** [docs/decisions/0017-agent-orchestration-protocol.md](../decisions/0017-agent-orchestration-protocol.md)
**Depends on:** Phase (a) test plan ([docs/test-plans/agent-orchestration-phase-a.md](agent-orchestration-phase-a.md))
**Status:** authored — awaiting phase (b) implementation
**Date:** 2026-08-15

---

## Interfaces This Plan Depends On

The following signatures are extracted from the design doc (§3, §4, §7, §8, §11b). When the
engineer changes any of these, update the corresponding test cases and this section.

| Interface | Expected signature / shape | Design ref |
|---|---|---|
| `tasks.state` machine | Six valid states: `submitted`, `working`, `input-required`, `completed`, `failed`, `escalated`; all transitions are master-computed | §3 |
| `tasks.queued_position` column (or equivalent queue table) | Tracks FIFO position for delegations waiting on the per-topic lock; `NULL` when not queued | §8, §11b |
| `POST /orchestrate/submit_result` | REST endpoint + MCP tool: records result-of-record on task row; stages judgment turn dispatch; available to depth-≥1 assignees only | §4.1, §4.3, §11b |
| `POST /orchestrate/answer_question` | REST endpoint + MCP tool: records answer to a pending `ask_sender` question; transitions task `input-required → working`; stages answer dispatch | §4.1, §4.4, §11b |
| `POST /orchestrate/accept_result` | REST endpoint + MCP tool: closes a task as `completed`; available to the task's dispatcher only, on the judgment turn | §4.1, §4.4, §11b |
| `SubmitResult` | `{task_id: str, state: 'working'}` | §4.1 |
| `AnswerResult` | `{task_id: str, state: 'working'}` | §4.1 |
| `AcceptResult` | `{task_id: str, state: 'completed'}` | §4.1 |
| `DelegateResult` (updated) | `{task_id: str, state: 'submitted' | 'queued', queued_position: int | None}` — `queued_position` non-null iff state is `queued` | §4.1, §8 |
| Per-topic lock | DB-backed lock keyed by `topic_id`; acquired at `submitted → working`; released only on `completed` or `failed`; retained through `escalated` | §8 |
| FIFO queue | Per-topic ordered list of delegations waiting on the lock; auto-dispatch on lock release; durable across process restart | §8 |
| `_save_agent_response` re-entry hook | Extended to: check staged tool messages in `pending_dispatches`; dispatch staged messages via `dispatch_to_staff` when turn ends; apply the implicit-result fallback (a question turn ending without `answer_question` fails upward to the user — revised in rc8, see design §4.1) | §7 |
| `pending_dispatches` map | In-memory map `message_id → {task_id, dispatcher_kind, dispatcher_name, turn_kind}`; rebuilt from DB on startup | §7 |
| `GET /orchestrate/tasks` | Lists tasks for a topic; query param `state` optional filter; returns task rows | §11b |
| `DISPATCH_TOKEN` auth | Short-lived token injected into the MQTT prompt payload (not stored in messages or returned by GET/WS endpoints); validated by all three `POST /orchestrate/…` endpoints instead of `caller_mismatch` message-id binding | §4 (auth note) |
| Config knobs (`ORCH_MAX_DELEGATION_DEPTH`, `ORCH_MAX_TASKS_PER_ROOT`) | Read from `config` table (scope `workspace` then `global`); fall through to hard-coded default | §5, §11b |
| MCP tool availability by turn context | `submit_result` available to depth-≥1 turns; `answer_question` available when a question on a caller-owned task is pending; `accept_result` available to dispatcher on judgment turn | §4.2 |
| Free-text silent rule | On a tool-staged turn where staged target == dispatcher: free text stored `silent=1`; not sent to user | §7 |
| Staff-message invariant | Every staff-addressed message originates from an MCP tool call; free text from a non-tool-calling agent is never silently routed to another staff | §4.2 |
| `GET /orchestrate/tasks` (token) | Token must NOT appear in the response body or headers | (token non-leak req) |
| WS frames | `DISPATCH_TOKEN` must NOT appear in any WebSocket broadcast frame | (token non-leak req) |

---

## Pass/Fail Criteria

A test **passes** when:
- The observed behaviour matches the expected result precisely.
- For `automated` tests: assertion is machine-verifiable without human input.
- For `needs-human` tests: a human reviewer has confirmed the expected visual or interactive
  behaviour via the staging UI.
- For `deferred` cases: explicitly out of scope for this plan; listed for traceability.

A test **fails** when any assertion is violated, an unexpected exception is raised, or a
`needs-human` case is declined during UAT signoff.

---

## Scope

Phase (b) only. Specifically:

- Turn re-entry: `_save_agent_response` dispatches staged tool messages and applies implicit
  fallbacks when the agent's `/response` arrives.
- `pending_dispatches` in-memory map and startup rebuild from open tasks.
- Task state machine transitions `submitted → working → input-required → working → completed`;
  machine enforcement (illegal transitions return MCP errors, state does not change).
- `submit_result`, `answer_question`, and `accept_result` MCP tools and their REST endpoints.
- Implicit-result fallback (delegated turn ends without `submit_result`); answer-missing fail-upward
  fallback (question turn ends without `answer_question`).
- Per-topic DB-backed lock: acquired on `submitted → working`, released on `completed` / `failed`.
  Lock retained through `escalated` state (escalation is phase (c) but the lock-retention
  invariant is implemented in phase (b) as part of the queue drain logic).
- FIFO queue: `delegations that arrive while lock is held return `state='queued'`;
  auto-dispatch on lock release; queue is durable across restart.
- `DISPATCH_TOKEN` auth: short-lived token in MQTT prompt payload; validated by orchestration
  endpoints; supersedes phase-(a) `caller_mismatch` message-id binding; token never exposed
  via GET, WS frames, or any public-facing payload.
- Config knobs `ORCH_MAX_DELEGATION_DEPTH` and `ORCH_MAX_TASKS_PER_ROOT` read from the
  `config` table via the staff-cascade; hard-coded defaults remain as fallback.
- `GET /workspaces/{wid}/topics/{tid}/orchestrate/tasks` list endpoint.
- Event-action loop safety: turn re-entry dispatches (`sender='event'`) must not fire
  `topic_message_sent` or `topic_message_received` actions.
- UI: tasks panel sidebar + task chip on message bubbles.

## Out of Scope

The following are explicitly deferred to Phase (c) or beyond:

- `reject_result` and `give_up_task` MCP tools.
- Failure scoring (`failure_score`, `question_weight`, `max_failure_score`).
- Escalation state, direct user↔assignee channel, escalation modal UI.
- Escalation-close digest injection.
- `escalated → working` (Resume/Reassign) and `escalated → failed` (Cancel) transitions.
- Cross-topic delegation.
- Full A2A wire-format compliance.
- UAT execution against the live staging singleton — deferred to step 15 of the feature workflow.

---

## Test Matrix

Cases are grouped by area. Each row carries:

- **ID** — referenced in `tests/master/test_orchestration_phase_b.py` unless noted.
- **Kind** — `automated`, `needs-human`, or `deferred`.
- **Priority** — `P0` (blocking), `P1` (high), `P2` (normal).

---

### Area 1: Full Async Depth-1 Chain End-to-End

The primary integration smoke-test for Phase (b). Drives the full
`user → architect → delegate → engineer → submit_result → architect (judgment) → accept_result → user`
chain via REST/MQTT and asserts that every hop lands correctly in `messages`, every state
transition fires, and no extra events are emitted.

| ID | Case | Kind | Priority |
|---|---|---|---|
| E2E-01 | User posts to `@architect`; architect calls `delegate_task`; turn ends via `/response`; master dispatches first prompt to `@engineer`; engineer's session receives it; task transitions `submitted → working` | automated | P0 |
| E2E-02 | `@engineer` calls `submit_result(status='completed', summary=..., artifacts=[...])`; turn ends via `/response`; master dispatches judgment turn to `@architect`; task stays `working`; result-of-record written to task row | automated | P0 |
| E2E-03 | `@architect` calls `accept_result(task_id)`; turn ends via `/response`; task transitions `working → completed`; per-topic lock released | automated | P0 |
| E2E-04 | After lock release, `@architect`'s free-text reply in the judgment turn is broadcast to the user (receiver = user); message row has `receiver_kind='user'` | automated | P0 |
| E2E-05 | All six `messages` rows (user prompt, engineer prompt, engineer reply/submit, architect judgment prompt, architect accept-result, final reply) share the same `topic_id` and are ordered by `created_at` | automated | P0 |
| E2E-06 | No `topic_message_sent` or `topic_message_received` event actions fired for any of the internal staff-to-staff hops (sender=event re-entry messages); only the original user message and the final architect→user reply may trigger event actions | automated | P0 |
| E2E-07 | `pending_dispatches` map is empty after the full chain completes (no orphaned pending entries) | automated | P1 |

---

### Area 2: Ask / Answer Input-Required Round-Trip

| ID | Case | Kind | Priority |
|---|---|---|---|
| ASK2-01 | `@engineer` calls `ask_sender("which format?")`; turn ends via `/response`; master dispatches question to `@architect`; task transitions `working → input-required`; question message row has `receiver_kind='staff'`, `receiver_name='architect'`, `task_id=T` | automated | P0 |
| ASK2-02 | `@architect` calls `answer_question(task_id=T, answer="use JWT")`; turn ends via `/response`; master dispatches answer to `@engineer`; task transitions `input-required → working`; answer message row has `sender_kind='staff'`, `sender_name='architect'`, `receiver_kind='staff'`, `receiver_name='engineer'` | automated | P0 |
| ASK2-03 | After ASK2-02, `@engineer` receives the answer as its next prompt and continues normally (task state = `working`) | automated | P0 |
| ASK2-04 | Multi-question sequence: engineer asks question 1; architect answers; task back to `working`; engineer asks question 2; architect answers again; task back to `working`; all four Q/A message rows share `task_id=T` | automated | P1 |
| ASK2-05 | Question bubbles: engineer asks dispatcher; dispatcher (architect) does not know; architect issues its own `ask_sender` to the user; task at depth-1 is `input-required`; user's answer routes to architect; architect's turn re-enters; architect answers engineer | automated | P1 |
| ASK2-06 | `answer_question` called by a staff who is NOT the task's dispatcher returns a tool error; task state does not change; no dispatch is staged | automated | P0 |
| ASK2-07 | `answer_question` called when the task is NOT in `input-required` state returns a state-machine error (illegal transition); task state unchanged | automated | P0 |

---

### Area 3: Implicit Fallbacks

| ID | Case | Kind | Priority |
|---|---|---|---|
| IMPL-01 | Delegated turn ends via `/response` without any `submit_result` call; master synthesises implicit result: `status='completed'`, `summary=<turn's last_response text>`, `artifacts=[]`; judgment turn dispatched to dispatcher | automated | P0 |
| IMPL-02 | Implicit-result path: result-of-record on the task row is set (not NULL); `result_summary` equals the agent's reply text | automated | P0 |
| IMPL-03 | Question turn (`input-required`) ends via `/response` without any `answer_question` call; master uses pending-dispatch context to treat the turn's final reply text as the answer; answer dispatched to assignee; task transitions `input-required → working` | automated | P0 |
| IMPL-04 | Implicit-answer path: the answer message row has `sender_kind='staff'`, `sender_name=<dispatcher>`, `receiver_kind='staff'`, `receiver_name=<assignee>`, `task_id=T` | automated | P0 |
| IMPL-05 | Judgment turn (dispatcher receives result) ends via `/response` without `accept_result`; per design §4.4 phase-b documented behaviour: no state transition; the free-text reply is still stored with `silent=0` and routed to the user as a normal reply; a `guard_hit` log line is emitted (`guard=judgment_stall`); task remains `working` | automated | P1 |
| IMPL-06 | An agent at depth 0 (no delegated task context) whose turn ends without any tool call stores the reply normally (receiver = user); no implicit-result logic is triggered; no pending_dispatches entry created | automated | P1 |

---

### Area 4: State Machine Enforcement — Illegal Transitions

All illegal-transition attempts must return an MCP tool error (HTTP 409 on the REST endpoint);
task state must not change; no dispatch must be staged.

| ID | Case | Kind | Priority |
|---|---|---|---|
| ILLS-01 | `accept_result` called on a task in `input-required` state → 409; state stays `input-required` | automated | P0 |
| ILLS-02 | `submit_result` called by the task's dispatcher (not the assignee) → tool error; state unchanged | automated | P0 |
| ILLS-03 | `answer_question` called by the task's assignee (not the dispatcher) → tool error; state unchanged | automated | P0 |
| ILLS-04 | `accept_result` called twice on the same task (double-accept) → second call returns 409 because task is already `completed`; state stays `completed` | automated | P0 |
| ILLS-05 | `accept_result` called on a task that is `submitted` (not yet dispatched) → 409; state stays `submitted` | automated | P1 |
| ILLS-06 | `submit_result` called when no active delegated task exists for the caller's turn context → tool error with descriptive code; no row mutation | automated | P0 |
| ILLS-07 | Illegal transition error is logged at WARN level: `orchestration.guard_hit guard=<name> task_id=<T> topic_id=<…>` | automated | P1 |

---

### Area 5: `submit_result` MCP Tool and REST Endpoint

| ID | Case | Kind | Priority |
|---|---|---|---|
| SUBM-01 | `submit_result(status='completed', summary='done', artifacts=[])` returns `SubmitResult` with `{task_id, state='working'}` (state stays `working` until dispatcher accepts) | automated | P0 |
| SUBM-02 | Task row after `submit_result`: `result_summary` set, `result_artifacts` matches artifacts JSON, `state='working'`, `closed_at=NULL` | automated | P0 |
| SUBM-03 | Calling `submit_result` twice in the same turn overwrites the result-of-record; the second call's summary is what the dispatcher sees (last-call-wins) | automated | P1 |
| SUBM-04 | `submit_result` with `status='failed'` is accepted; `result_summary` stored; task stays `working` until dispatcher accepts/rejects | automated | P1 |
| SUBM-05 | `submit_result` triggers staging of the judgment turn; when the turn ends (`/response`), the staged judgment prompt is dispatched to the dispatcher via `dispatch_to_staff` | automated | P0 |
| SUBM-06 | `submit_result` endpoint returns 409 if the calling staff is not the task's `assignee_name` | automated | P0 |
| SUBM-07 | `submit_result` is present in the MCP tool list for depth-≥1 turns; absent for depth-0 turns | automated | P0 |

---

### Area 6: `answer_question` MCP Tool and REST Endpoint

| ID | Case | Kind | Priority |
|---|---|---|---|
| ANS-01 | `answer_question(task_id=T, answer="use JWT")` returns `AnswerResult` with `{task_id=T, state='working'}` | automated | P0 |
| ANS-02 | Task row after `answer_question`: `state='working'`, `updated_at` refreshed | automated | P0 |
| ANS-03 | Answer message row: `sender_kind='staff'`, `sender_name=<dispatcher>`, `receiver_kind='staff'`, `receiver_name=<assignee>`, `task_id=T`, `reply_to_message_id` = the question message | automated | P0 |
| ANS-04 | `answer_question` is present in the MCP tool list when a question on a caller-owned pending task exists; absent otherwise | automated | P1 |
| ANS-05 | `answer_question` on a task that does not belong to the calling dispatcher returns 403/422; state unchanged | automated | P0 |
| ANS-06 | `answer_question` for an unknown `task_id` returns 404 | automated | P1 |

---

### Area 7: `accept_result` MCP Tool and REST Endpoint

| ID | Case | Kind | Priority |
|---|---|---|---|
| ACCPT-01 | `accept_result(task_id=T)` returns `AcceptResult` with `{task_id=T, state='completed'}` | automated | P0 |
| ACCPT-02 | Task row after `accept_result`: `state='completed'`, `closed_at` non-null, `updated_at` refreshed | automated | P0 |
| ACCPT-03 | Per-topic lock is released on `accept_result`; a queued delegation (if any) is auto-dispatched | automated | P0 |
| ACCPT-04 | `accept_result` is present in the MCP tool list on the dispatcher's judgment turn; absent on non-judgment turns | automated | P1 |
| ACCPT-05 | `accept_result` by a staff who is not the task's `dispatcher_name` returns 403/422; state unchanged | automated | P0 |
| ACCPT-06 | `accept_result` for an unknown `task_id` returns 404 | automated | P1 |

---

### Area 8: Token Auth (DISPATCH_TOKEN)

Token auth supersedes the phase-a `caller_mismatch` message-id binding for `POST /orchestrate/*`
endpoints. The token is injected into the MQTT prompt payload by `dispatch_to_staff` and is
validated by all three new endpoints as well as the existing `delegate` and `ask` endpoints.

| ID | Case | Kind | Priority |
|---|---|---|---|
| TOKN-01 | Valid `DISPATCH_TOKEN` in request body (or header per engineer's implementation) is accepted by `POST /orchestrate/submit_result`, `answer_question`, `accept_result`, `delegate`, and `ask` | automated | P0 |
| TOKN-02 | Missing `DISPATCH_TOKEN` returns 401 or 422 with `missing_token`; no state mutation | automated | P0 |
| TOKN-03 | Wrong `DISPATCH_TOKEN` (valid format but wrong value) returns 401 or 422 with `invalid_token`; no state mutation | automated | P0 |
| TOKN-04 | Token replayed against a different `message_id` than it was issued for is rejected; no state mutation | automated | P0 |
| TOKN-05 | `DISPATCH_TOKEN` is NOT present in the `GET /orchestrate/tasks` response body or headers | automated | P0 |
| TOKN-06 | `DISPATCH_TOKEN` is NOT present in any WebSocket broadcast frame (`type=message`, `type=chunk`, any type) | automated | P0 |
| TOKN-07 | `DISPATCH_TOKEN` is NOT present in the `GET /workspaces/{wid}/topics/{tid}/messages` response | automated | P0 |
| TOKN-08 | `DISPATCH_TOKEN` is NOT returned in the MQTT prompt payload field for any field accessible from within the agent's normal tool output (token must only appear in the env-injected context, never echoed back) | automated | P1 |
| TOKN-09 | Token has a short lifetime; a token older than its TTL is rejected with `expired_token`; no state mutation | automated | P1 |

---

### Area 9: Per-Topic Lock and FIFO Queue

| ID | Case | Kind | Priority |
|---|---|---|---|
| LOCK-01 | `delegate_task` while no other delegation is active acquires the lock and dispatches immediately; response is `{state='submitted'/'working', queued_position=null}` | automated | P0 |
| LOCK-02 | Second `delegate_task` on the same topic while the first task's lock is held returns `{state='queued', queued_position=1}` | automated | P0 |
| LOCK-03 | Third `delegate_task` on the same topic while lock is held returns `{state='queued', queued_position=2}`; queue is strictly FIFO | automated | P1 |
| LOCK-04 | On `accept_result` (lock release), the first queued delegation auto-dispatches; its task row transitions `submitted → working`; `queued_position` cleared | automated | P0 |
| LOCK-05 | Queue drain order: two queued delegations drain in FIFO order after the first task completes, then the second completes | automated | P1 |
| LOCK-06 | Two dispatchers for the same assignee (`@engineer`) on the same topic: both dispatch correctly when their respective locks are acquired; routing stays correct via `task_id` | automated | P1 |
| LOCK-07 | Restart durability: with a queued delegation in flight, process restarts; on restart, the queue is rebuilt from staged rows; the next `accept_result` still drains the queue and dispatches the pending delegation | automated | P0 |
| LOCK-08 | Lock is NOT acquired by depth-0 plain user→staff messages (non-`delegate_task` dispatches); the user can message `@architect` while `@engineer` holds the lock | automated | P1 |
| LOCK-09 | Escalated task retains the lock: while a task is in `escalated` state, a new `delegate_task` on the same topic returns `state='queued'` (escalation is a phase-c state, but the lock-retention invariant is asserted here via direct DB manipulation) | automated | P1 |

---

### Area 10: Staged Tool Messages and Free-Text Silent Rule

| ID | Case | Kind | Priority |
|---|---|---|---|
| STGD-01 | `ask_sender` during a turn stages a message to the dispatcher; when `/response` arrives, master dispatches exactly one message to the dispatcher; no duplicate dispatch | automated | P0 |
| STGD-02 | `submit_result` during a turn stages a judgment prompt to the dispatcher; when `/response` arrives, master dispatches exactly one judgment prompt | automated | P0 |
| STGD-03 | `answer_question` during a turn stages an answer to the assignee; when `/response` arrives, master dispatches exactly one answer to the assignee | automated | P0 |
| STGD-04 | When a staged tool message targets the same receiver as the turn-taker's dispatcher, the turn's free text is stored `silent=1` (auditable, not rendered); the staged message is what renders | automated | P0 |
| STGD-05 | When a staged tool message targets a different receiver than the dispatcher (e.g. `delegate_task` turn: staged message to engineer, free text to user), both the staged message AND the free text flow (free text is rendered to user; staged message dispatched to engineer) | automated | P0 |
| STGD-06 | No tool-message staging: plain agent turn with no MCP tool calls stores the reply `silent=0` and routes to the user normally; `pending_dispatches` has no entry for this turn | automated | P1 |
| STGD-07 | `accept_result` is a state-change-only call: no outbound staff message is staged; the turn's free text is the judgment turn's final reply and routes to the user | automated | P0 |
| STGD-08 | Staff-message invariant: an agent turn that calls no MCP tools never silently routes free text to another staff; all free-text output goes to the turn-taker's dispatcher (or user) | automated | P0 |

---

### Area 11: `pending_dispatches` Map Durability

| ID | Case | Kind | Priority |
|---|---|---|---|
| PDUR-01 | Startup scan builds `pending_dispatches` from all open tasks (state not in `completed`, `failed`); after scan, a completed turn can still be processed correctly | automated | P0 |
| PDUR-02 | A task that is `completed` or `failed` is NOT included in the startup scan (does not pollute the map) | automated | P0 |
| PDUR-03 | An open task with no un-responded staff message (e.g. just created, no `/response` yet pending) is included in the scan without causing a dispatch until a `/response` arrives | automated | P1 |
| PDUR-04 | Process restart mid-turn: a task is `working`, the assignee's response arrives after restart; pending_dispatches rebuilt from DB; the judgment turn dispatch proceeds correctly | automated | P1 |
| PDUR-05 | Process restart with a queued delegation: queue position is correctly reconstructed; on next `accept_result`, the queued delegation dispatches in FIFO order | automated | P0 |

---

### Area 12: Config Knobs via Config-Table Cascade

| ID | Case | Kind | Priority |
|---|---|---|---|
| CFG-01 | `ORCH_MAX_DELEGATION_DEPTH` set to `2` in the `config` table at workspace scope overrides the hard-coded default of `1`; a depth-2 delegation succeeds instead of being rejected | automated | P0 |
| CFG-02 | `ORCH_MAX_TASKS_PER_ROOT` set to `5` at workspace scope; delegation that would create the 6th task under the same root returns `fan_out_exceeded` | automated | P0 |
| CFG-03 | Workspace-level config overrides global default; a second workspace with no workspace-level override uses the global or hard-coded default | automated | P1 |
| CFG-04 | Global scope config (`scope_type='global'`) supplies the fallback when workspace has no override; hard-coded default is the last resort | automated | P1 |
| CFG-05 | Non-integer or out-of-range config value for `ORCH_MAX_DELEGATION_DEPTH` is treated as the hard-coded default (no crash, no silent acceptance) | automated | P1 |

---

### Area 13: `GET /orchestrate/tasks` Endpoint

| ID | Case | Kind | Priority |
|---|---|---|---|
| TSKS-01 | `GET /orchestrate/tasks` for a topic with no tasks returns `[]` | automated | P0 |
| TSKS-02 | `GET /orchestrate/tasks` returns all task rows for the topic with at least `{id, state, depth, dispatcher_kind, dispatcher_name, assignee_name, goal, queued_position, created_at}` | automated | P0 |
| TSKS-03 | `GET /orchestrate/tasks?state=working` returns only tasks in `working` state | automated | P1 |
| TSKS-04 | `GET /orchestrate/tasks` does not expose `DISPATCH_TOKEN` in any row | automated | P0 |
| TSKS-05 | `GET /orchestrate/tasks` for an unknown `topic_id` returns 404 | automated | P1 |
| TSKS-06 | Completed tasks (`state='completed'`) appear in the response (not filtered out); clients can show historical tasks | automated | P1 |

---

### Area 14: Event-Action Loop Safety

This is a critical regression area. Every staff-to-staff re-entry dispatch uses `sender='event'`
and `sender_kind='staff'`. The event-action infrastructure must not fire on these messages.

| ID | Case | Kind | Priority |
|---|---|---|---|
| EVNT-01 | `topic_message_sent` event action does NOT fire when a turn re-entry dispatch arrives (engineer → architect judgment turn); verified by checking event_runs table after a full chain | automated | P0 |
| EVNT-02 | `topic_message_received` event action does NOT fire when an agent replies to an event-dispatched prompt (turn re-entry reply); the `_prompt_was_event_dispatched` guard must fire | automated | P0 |
| EVNT-03 | `topic_message_sent` DOES fire for a genuine user-originated message after a completed delegation chain; backward-compat gate is intact | automated | P0 |
| EVNT-04 | `topic_message_received` DOES fire when an agent replies to a user-originated prompt (non-orchestration turn) | automated | P0 |
| EVNT-05 | A delegation chain with three internal hops (user→architect, architect→engineer, engineer→architect judgment) produces exactly one `topic_message_sent` event (the user message) and one `topic_message_received` event (architect's final user-facing reply) | automated | P1 |

---

### Area 15: Backward Compatibility

Phase (b) must not break any phase-(a) or pre-orchestration behaviour.

| ID | Case | Kind | Priority |
|---|---|---|---|
| BCKB-01 | Plain topic (single staff, no orchestration): user message → staff reply works identically to before; no `tasks` row created; no lock acquired | automated | P0 |
| BCKB-02 | Phase-(a) `delegate_task` + `ask_sender` flows (without `submit_result` / `accept_result`) still work correctly; implicit-result fallback handles the submit-less turn gracefully | automated | P0 |
| BCKB-03 | `dispatch_to_staff` called with no orchestration kwargs (legacy path) produces a payload with `task_id=null`, `task_depth=0`, no new staging; MQTT payload shape is unchanged | automated | P0 |
| BCKB-04 | Existing `messages` rows (pre-phase-b) with no `task_id` are not affected by the new startup scan; `pending_dispatches` map stays empty for those topics | automated | P0 |
| BCKB-05 | `DelegateResult` from phase-(a) `POST /orchestrate/delegate` now includes `queued_position` field (null when not queued); any existing caller that ignores the new field continues to work | automated | P0 |
| BCKB-06 | Phase-(a) test suite (`tests/master/test_orchestration.py`) remains fully green with phase-(b) code deployed; no regressions | automated | P0 |
| BCKB-07 | UAT (staging): existing topics continue working after phase-(b) deploy — send a message, receive a reply, no regressions | needs-human | P0 |

---

### Area 16: UI — Tasks Panel and Task Chip

All needs-human cases require human verification on the staging UI.

| ID | Case | Kind | Priority |
|---|---|---|---|
| UI2-01 | Tasks panel visible in the topic sidebar; lists active tasks with state, assignee, and goal | needs-human | P0 |
| UI2-02 | A task in `working` state shows a spinner or progress indicator in the tasks panel | needs-human | P1 |
| UI2-03 | A task in `input-required` state shows a distinct indicator (e.g. question-mark badge) in the tasks panel | needs-human | P0 |
| UI2-04 | A task in `completed` state shows a green checkmark or similar in the tasks panel | needs-human | P1 |
| UI2-05 | Clicking a task in the panel filters the topic message view to show only messages with that `task_id` | needs-human | P0 |
| UI2-06 | A task chip on a message bubble links to the task in the tasks panel; clicking the chip filters to that task | needs-human | P0 |
| UI2-07 | A queued delegation (state = `queued`) shows a queue-position badge in the tasks panel | needs-human | P1 |
| UI2-08 | After `accept_result`, the task disappears from the "active" view in the tasks panel and appears in a "completed" or "history" section | needs-human | P2 |
| UI2-09 | Plain user messages (no `task_id`) are not filtered out when no task filter is active; filtering by one task hides plain messages | needs-human | P1 |
| UI2-10 | A message with `silent=1` is NOT rendered in the UI; only the structured tool output is shown | needs-human | P0 |

---

## Edge Cases

- **E2E-06 (event-action loop).** The gate relies on `sender_kind='user'` for the
  `topic_message_sent` predicate and `_prompt_was_event_dispatched` for the response path.
  Both must be verified independently (EVNT-01 through EVNT-05). A regression here causes
  infinite dispatch loops that cannot be stopped without restarting the master process.

- **IMPL-05 (judgment stall, phase-b documented behaviour).** Per §4.4, when a judgment turn
  ends without `accept_result`, the design specifies routing the free text to the assignee
  as a scored clarification. In phase (b), the judgment-stall path is explicitly documented
  to emit `guard_hit guard=judgment_stall` and not execute the scored-clarification routing
  (scoring is phase-c). The test therefore asserts that: (1) guard is logged, (2) no new
  staff-to-staff dispatch is emitted, (3) the free text reaches the user, and (4) task
  state remains `working`. This is correct phase-b behaviour, not a failure.

- **LOCK-09 (escalated-keeps-lock).** Escalation is a phase-c state but the lock-retention
  invariant must hold from phase (b) so that the phase-c escalation path gets a coherent
  worktree. This case inserts an `escalated` task row directly into the DB to bypass
  phase-c guards and asserts the lock is still counted as held.

- **TOKN-04 (token replay).** A valid token bound to message M1 must be rejected when
  presented alongside message M2. The token must encode the `message_id` it was issued for
  and validate the binding on every call.

- **STGD-04 vs STGD-05 (silent vs. both-flow).** The disambiguation criterion is: does the
  staged tool message target the same receiver as the turn-taker's own dispatcher? If yes,
  the free text is silent. If no (e.g. delegation: staged goes to assignee, dispatcher is user),
  both flow. Both paths must be explicitly tested to avoid accidental double-silencing or
  double-sending.

- **PDUR-07 / LOCK-07 (restart durability with active queue).** The pending_dispatches map
  is in-memory; the queue must be persisted in the DB. A process restart between a
  `delegate_task` (queued) and the lock-release `accept_result` must not lose the queued
  delegation or fire it out of FIFO order.

- **CFG-05 (invalid config values).** The config cascade must treat any non-integer or
  negative value for depth/tasks-per-root as if the key were absent (fall through to default).
  Accepting a corrupt config value silently could raise a depth of 0 (blocking all delegation)
  or an absurdly large tasks-per-root (bypassing the fan-out fuse).

---

## Failure Modes

| Failure mode | Expected behaviour | Test ID(s) |
|---|---|---|
| `submit_result` called by dispatcher (not assignee) | 403/422 tool error; no staging; state unchanged | SUBM-06, ILLS-02 |
| `accept_result` on `input-required` task | 409; state unchanged; guard_hit logged | ILLS-01 |
| `accept_result` on already-completed task | 409; state unchanged | ILLS-04 |
| `answer_question` by the assignee (not dispatcher) | Tool error; state unchanged | ILLS-03, ASK2-06 |
| Delegated turn ends with no `submit_result` | Implicit result synthesised; judgment turn dispatched | IMPL-01, IMPL-02 |
| Question turn ends with no `answer_question` | Implicit answer; answer dispatched to assignee | IMPL-03, IMPL-04 |
| Judgment turn ends with no `accept_result` | Guard logged; no staff-to-staff dispatch; free text → user; task stays `working` | IMPL-05 |
| Wrong/missing DISPATCH_TOKEN | 401/422; no state mutation | TOKN-02, TOKN-03 |
| Token replayed on different message | 401/422; no state mutation | TOKN-04 |
| Lock held; second `delegate_task` | Returns `state='queued'`; position tracked | LOCK-02 |
| Process restart with queue in-flight | Queue rebuilt from DB; FIFO drain resumes correctly | LOCK-07, PDUR-05 |
| Config value invalid | Falls through to hard-coded default; no crash | CFG-05 |
| Invalid state for config knob | Delegation depth treated as default; request proceeds normally | CFG-05 |

---

## Non-Functional Requirements

| Requirement | Verification approach |
|---|---|
| Phase-(a) test suite stays green | BCKB-06 — run `tests/master/test_orchestration.py` against phase-b code |
| Turn re-entry dispatches never fire event-action hooks | EVNT-01 through EVNT-05 |
| DISPATCH_TOKEN never appears in any GET, WS, or public REST response | TOKN-05, TOKN-06, TOKN-07 |
| `pending_dispatches` map survives process restart for all open tasks | PDUR-01 through PDUR-05 |
| Per-topic lock is DB-backed (not in-memory only) for restart durability | LOCK-07 |
| Free text on a tool-staged turn is stored `silent=1` (not dropped) — auditable in DB | STGD-04 |
| All guard hits produce exactly one `orchestration.guard_hit` log line (no missing, no duplicates) | ILLS-07, EVNT-01 |
| Config knob resolution is cascade: workspace → global → hard-coded default | CFG-03, CFG-04 |

---

## Out of Scope for This Test Plan

- `reject_result` and `give_up_task` MCP tools — Phase (c).
- Failure scoring (`failure_score`, `question_weight`, `max_failure_score`) — Phase (c).
- Escalation-open, escalation-close digest, and escalation modal UI — Phase (c).
- `escalated → working` (Resume/Reassign) and `escalated → failed` (Cancel) — Phase (c).
- Escalation `staff→user` direct channel and red-border badge — Phase (c).
- Deep-hierarchy (depth > 2) tree view in tasks panel — future work.
- Cross-topic delegation — out of scope for all phases.
- Full A2A wire-format compliance — out of scope for all phases.
- UAT execution against the live staging singleton — deferred to step 15 of the feature workflow.
