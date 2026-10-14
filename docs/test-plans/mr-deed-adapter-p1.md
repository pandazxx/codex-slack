# Test Plan: Mr-deed Agent Adapter — Phase 1 (P1)

**Feature:** Mr-deed in-process agent adapter — P1: runner, streaming, session resume, cancel
**Design doc:** [docs/design/mr-deed-adapter.md](../design/mr-deed-adapter.md)
**ADR:** [docs/decisions/0018-mr-deed-agent-adapter.md](../decisions/0018-mr-deed-agent-adapter.md)
**Status:** authored — awaiting implementation
**Date:** 2026-10-14

---

## Interfaces This Plan Depends On

The following are extracted from the design doc and the mr-deed `v0.1.0` public API. If the
engineer changes any of these signatures, update the corresponding test cases and this section.

| Interface | Expected signature / shape | Design ref |
|---|---|---|
| `_VALID_ADAPTERS` (master) | Set includes `"mr-deed"` | §1 touch-points |
| `staffs.agent_config` column | Nullable `TEXT`; additive migration; existing rows `NULL` | §1 touch-points |
| `StaffIn` / `StaffOut` (master) | Both include optional `agent_config: str \| None` | §1 touch-points |
| Staff CRUD (global/workspace/topic) | Accept, store, and return `agent_config` | §1 touch-points |
| `agent_config` validation on save | Reject non-mapping YAML with HTTP 422; pass on empty/null or valid mapping | §3 |
| Staff cascade for `agent_config` | Topic staff fully replaces workspace/global staff (full-override, no field merge) | §3 |
| Dispatch payload | Includes `agent_config` field (string or null) sourced from resolved staff | §2 |
| `_process_prompt` (agent) | `elif adapter == "mr-deed": _run_mr_deed(...)` branch | §1 touch-points |
| `mr_deed_runner.resolve_config(agent_config, worktree, name)` | Tier 0 (payload `agent_config`) → tier 2 (`/opt/codex-slack/config/mr-deed/<name>.yaml`) → tier 3 (mr-deed built-ins); default name `"default"` when `staff.agent` is empty | §3 |
| `_run_mr_deed` — new session | `DefaultAgent(model, env, tools=(), on_event=publish, cancel_event=..., output_path=<traj>, **config).run(task)` | §5 |
| `_run_mr_deed` — resume | `DefaultAgent.from_trajectory(<traj>, model, env, tools=..., on_event=..., **current_config).continue_run(task)` | §5 |
| Trajectory path | `/workspace/sessions/mr-deed/<session_id>.traj.json` | §5 |
| Missing trajectory on resume | Treated as new session (not an error) | §5 |
| Corrupted / unreadable trajectory on resume | Error reply naming the file; trajectory not overwritten | §5 (specified here) |
| `on_event` → chunk mapping | `mr_deed.assistant`, `mr_deed.action`, `mr_deed.observation`, `mr_deed.format_error`, `mr_deed.exit`; system/user not published | §6 |
| MQTT publish failure inside `on_event` | Caught and logged; turn continues | §6 |
| Exit-status mapping | Submitted → normal reply; LimitsExceeded, TimeExceeded, RepeatedFormatError, Cancelled, provider exception, other exception, invalid config → named error replies per §7 table | §7 |
| Running-agent registry | Keyed by `reply_message_id`; removed in `finally`; cancel arriving before registration applied right after | §8 |
| Per-turn env dict keys | `DISPATCH_TOKEN`, `TASK_DEPTH`, `TOPIC_ID`, `AGENT_NAME`, `PROMPT_MESSAGE_ID`, `WORKSPACE_ID`, `MASTER_URL` — all go to `LocalEnvironment(env=...)`, never to `os.environ` | §9 |
| `import mr_deed_runner` | Does not modify `os.environ`, does not import `mr_deed.run` | §10 |
| `requirements.txt` pin | `mr-deed @ git+https://github.com/pandazxx/mr-deed@v0.1.0` | §11 |
| `TopicChat.vue` chunk classifier | `mr_deed.assistant` → `text`/`thinking`; `mr_deed.action` → `tool_use`; `mr_deed.observation` → `folded`; `mr_deed.format_error` → `parse_warning`; `mr_deed.exit` → `hidden` | §6 |
| Staff form UI | `adapter = mr-deed` shows `agent_config` YAML textarea; option `mr-deed` present in adapter dropdown | §1 touch-points |

---

## Design Decisions Taken for P1

Gaps found while authoring this plan, decided by the lead on 2026-10-14
(detailed-design level, within ADR-0018):

| # | Question | Decision |
|---|---|---|
| D1 | Corrupted / unreadable trajectory on resume | Error reply naming the file; the file is **not** overwritten or replaced (MD-U-39). |
| D2 | `agent_config` absent vs null in the payload | Key always present; `null` when the staff has none (MD-U-22). |
| D3 | Tier-0 merge semantics | Tier 0 is used as-is (first match wins), with staff overrides applied on top. The overlay alternative stays an open question in the design doc. |
| D4 | `staff.agent` empty vs null | Both resolve to config name `default` (MD-U-27, MD-U-28). |
| D5 | Logging `agent_config` | Not logged verbatim: YAML may carry `model_kwargs.api_key`. Logs show a size marker only (MD-U-81). |

## Pass / Fail Criteria

A test **passes** when:
- The observed behaviour matches the expected result exactly.
- For `automated`: the assertion is machine-verifiable without human input.
- For `needs-human`: a human reviewer has confirmed the visual or interactive behaviour in the
  staging UI and replied with ✅ in the PR comment.

A test **fails** when any assertion is violated, an unexpected exception is raised, or a
`needs-human` case is declined.

---

## Scope

P1 specifically:

- `mr-deed` added to `_VALID_ADAPTERS`.
- Additive `staffs.agent_config TEXT` migration; `StaffIn`/`StaffOut` field; CRUD at all scopes.
- `agent_config` YAML validation on save (reject non-mapping, 422).
- Staff full-override cascade applies to `agent_config`.
- Dispatch payload carries resolved `agent_config`.
- `elif adapter == "mr-deed"` branch and `_run_mr_deed` / `mr_deed_runner.py`.
- Config resolution tiers 0, 2, 3 (`agent_config` payload → shipped `config/mr-deed/` → mr-deed built-ins).
- Session persist (`save()`) and resume (`from_trajectory` + `continue_run`).
- Streaming via `on_event` → `/chunk` pipeline.
- Cancel via `agent.cancel()` and running-agent registry.
- Per-hop env transport (§9 table).
- Concurrency isolation (two parallel turns, distinct `DISPATCH_TOKEN`).
- Packaging: `mr-deed @ ...@v0.1.0` pin in `requirements.txt`; import hygiene.
- Frontend: adapter option in staff forms, `agent_config` YAML textarea, `TopicChat.vue` classifier.
- Backward compat: `claude-code` and `codex` adapters unchanged; staffs without `agent_config` unchanged.

## Out of Scope (deferred)

| Area | Deferred to |
|---|---|
| Host tools (`delegate_task`, `ask_sender`, `submit_result`, notes.*) | P2 |
| `orchestration_tools_for` shared gating function | P2 |
| MCP server refactor to call-time context | P2 |
| Tier-1 config (`<worktree>/.prj_assistant/mr-deed/`) | P3 |
| Session history compaction | post-P1 ADR |
| Image-size measurement and split requirements | post-P1 (SRE + engineer) |
| Cross-repo mr-deed upgrade PRs | separate tag-bump PRs |

---

## References

| Document | Relevant sections |
|---|---|
| [docs/design/mr-deed-adapter.md](../design/mr-deed-adapter.md) | §1, §1a, §2, §3, §5, §6, §7, §8, §9, §10, §11, §13 P1, §14 |
| [docs/decisions/0018-mr-deed-agent-adapter.md](../decisions/0018-mr-deed-agent-adapter.md) | Confirmation (unit + UAT), Phasing |
| [docs/knowledge-base/lessons-learned.md](../knowledge-base/lessons-learned.md) | 2026-08-16 (one test per env hop), 2026-07-13 (fixtures mirror real API shape) |
| mr-deed `v0.1.0` source | `mr_deed.agents.default.DefaultAgent`, `mr_deed.environments.local.LocalEnvironment`, `mr_deed.models.test_models.DeterministicToolcallModel`, `make_toolcall_output` |

---

## Test Environment

| Level | Environment | Notes |
|---|---|---|
| Unit (MD-U-xx) | pytest in-process, no network | Use `DeterministicToolcallModel` / `DeterministicModel` from `mr_deed.models.test_models`. Fake `LocalEnvironment` via monkeypatch or a lightweight subclass. No API keys required. |
| Integration (MD-I-xx) | dev env (agent container on `DEV_DOCKER_HOST`) | Requires `sre` to spin up dev env. Still uses deterministic test models — no provider API keys required unless otherwise noted. |
| UAT (MD-UAT-xx) | staging singleton | Image-based deploy via `sre`. Real HTTP/MQTT/WebSocket flows. Provider API key required for UAT cases that exercise a real model (marked `needs-human`). |

---

## Test Case Tables

### Area 1: Packaging and Import Hygiene

Tests that the dependency pin is correct and that importing the runner has no side effects.

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-01 | `requirements.txt` contains the pinned mr-deed entry | Repo checkout | Grep `requirements.txt` for `mr-deed @ git+https://github.com/pandazxx/mr-deed@v0.1.0` | Exact string present, no other mr-deed line | §11 | — |
| MD-U-02 | Importing `mr_deed_runner` leaves `os.environ` unchanged | Snapshot `os.environ` before import | `import src.agent.mr_deed_runner` | `os.environ` dict is byte-for-byte identical after import | §10 | — |
| MD-U-03 | Importing `mr_deed_runner` does not import `mr_deed.run` | Monitor `sys.modules` | `import src.agent.mr_deed_runner` | `mr_deed.run` not in `sys.modules` | §10 | — |
| MD-U-04 | `import mr_deed` itself has no side effects on `os.environ` | Snapshot `os.environ` | `import mr_deed` | `os.environ` unchanged | §10 (R5) | — |

---

### Area 2: Master — Adapter Enum and DB Schema

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-05 | `"mr-deed"` is in `_VALID_ADAPTERS` | Fresh master module | Read `_VALID_ADAPTERS` | Set contains `"mr-deed"` | §1 touch-points | — |
| MD-U-06 | Additive migration adds nullable `agent_config TEXT` to `staffs` | DB with pre-migration schema | Run migration | `agent_config` column present, type TEXT, nullable; all pre-existing rows have `agent_config = NULL` | §1 touch-points | — |
| MD-U-07 | Migration is idempotent | DB already migrated | Run migration again | No exception; schema unchanged | §1 touch-points | — |
| MD-U-08 | `StaffOut` includes `agent_config` field | — | Serialize a staff with `agent_config=None` | `agent_config` key present in output with value `null` | §1 touch-points | — |
| MD-U-09 | `StaffIn` accepts `agent_config` | — | Construct `StaffIn` with `agent_config="model:\n  model_name: gpt-4o"` | No validation error | §1 touch-points | — |

---

### Area 3: Master — `agent_config` YAML Validation on Save

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-10 | Valid mapping YAML is accepted | POST `/staffs` with valid YAML mapping | Submit staff with `agent_config: "model:\n  model_name: gpt-4o\n"` | 200/201; staff stored | §3 | — |
| MD-U-11 | Empty string `agent_config` is accepted | — | Submit staff with `agent_config: ""` | 200/201 | §3 | — |
| MD-U-12 | `null` / omitted `agent_config` is accepted | — | Submit staff without `agent_config` key | 200/201; stored as `NULL` | §3 | — |
| MD-U-13 | Non-mapping YAML scalar rejected with 422 | — | Submit `agent_config: "just a string"` | 422; error message references `agent_config` | §3 | — |
| MD-U-14 | Non-mapping YAML list rejected with 422 | — | Submit `agent_config: "- item1\n- item2"` | 422 | §3 | — |
| MD-U-15 | Syntactically invalid YAML rejected with 422 | — | Submit `agent_config: "key: [unclosed"` | 422 | §3 | — |
| MD-U-16 | Master does not import `mr_deed` for YAML validation | Monitor `sys.modules` in master process | Call staff-save endpoint with valid YAML | `mr_deed` not in `sys.modules` after the call | §11 | — |

---

### Area 4: Master — Staff Cascade for `agent_config`

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-17 | Global staff `agent_config` returned when no narrower scope overrides | Global staff with `agent_config="A"` | Resolve staff for topic with no workspace/topic override | `agent_config = "A"` | §3 | — |
| MD-U-18 | Workspace staff fully replaces global staff `agent_config` (no field merge) | Global staff `agent_config="A"`; workspace staff with same name and `agent_config="B"` | Resolve staff for topic in that workspace | `agent_config = "B"` (not a merge of A and B) | §3 | — |
| MD-U-19 | Topic staff fully replaces workspace staff `agent_config` | Workspace staff `agent_config="B"`; topic staff `agent_config="C"` | Resolve staff for that topic | `agent_config = "C"` | §3 | — |
| MD-U-20 | Topic staff with `agent_config=null` fully replaces (does not inherit) narrower scope | Global staff `agent_config="A"`; topic staff `agent_config=null` | Resolve staff for topic | `agent_config = null` | §3 | — |

---

### Area 5: Master — Dispatch Payload

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-21 | Dispatch payload includes `agent_config` from resolved staff | Staff with `agent_config="X"` | Call dispatch logic; inspect MQTT payload | Payload field `agent_config = "X"` | §2 | — |
| MD-U-22 | Dispatch payload `agent_config` is `null` when staff has no config | Staff with `agent_config=null` | Dispatch; inspect MQTT payload | `agent_config` key present, value `null` (not absent) | §2 | — |
| MD-U-23 | Staffs without `agent_config` (pre-migration) produce unchanged dispatch payloads | Staff created before migration (no `agent_config` column value) | Dispatch to that staff | Payload shape matches pre-P1 shape for `claude-code`/`codex` staff; no new keys beyond `agent_config` | §1, backward compat | — |

---

### Area 6: Agent — Config Resolution

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-24 | Tier 0 wins when `agent_config` in payload is non-empty | Payload has `agent_config="model:\n  model_name: test"` | Call `resolve_config(agent_config, worktree, name)` | Returns the tier-0 YAML, not shipped or built-in | §3 | — |
| MD-U-25 | Tier 2 shipped config used when tier 0 empty and shipped file exists | `agent_config=""`, shipped file `/opt/codex-slack/config/mr-deed/myconf.yaml` present | Call `resolve_config("", worktree, "myconf")` | Returns content of shipped file | §3 | — |
| MD-U-26 | Tier 3 mr-deed built-in used when tier 0 empty and no shipped file | `agent_config=""`, no shipped file for name `mini` | Call `resolve_config("", worktree, "mini")` | Returns mr-deed's built-in `mini` config | §3 | — |
| MD-U-27 | Default config name is `"default"` when `staff.agent` is empty string | `staff.agent = ""`, `agent_config = ""` | Resolve config | Shipped `config/mr-deed/default.yaml` used (it shadows mr-deed's built-in `default.yaml`) | §3 | — |
| MD-U-28 | Default name is `"default"` when `staff.agent` is absent/null | `staff.agent = null` | Resolve config | Shipped `config/mr-deed/default.yaml` used (it shadows mr-deed's built-in `default.yaml`) | §3 | — |
| MD-U-29 | Unknown shipped config name with no tier-0 YAML produces error reply | `agent_config=""`, no shipped file, name `"nonexistent"`, mr-deed has no built-in `nonexistent` | `_run_mr_deed(payload)` | Error reply: `"mr-deed config 'nonexistent' is invalid: <reason>"` | §7 | — |
| MD-U-30 | Invalid tier-0 YAML (syntax error that passes master's mapping check but mr-deed rejects) produces error reply | `agent_config` passes 422-check as a mapping but mr-deed raises on load | `_run_mr_deed(payload)` | Error reply naming source and reason; turn does not crash the worker thread | §7 | — |

---

### Area 7: Agent — System-Prompt and Model Overrides

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-31 | `staff.model` overrides `model.model_name` in resolved config | Config has `model.model_name=X`; staff has `model=Y` | Inspect model passed to `DefaultAgent` | Model name is `Y` | §3 | — |
| MD-U-32 | `staff.system_prompt` is appended (not replaced) to `system_template` | Config has `system_template="SYS"`; staff has `system_prompt="EXTRA"` | Inspect system template passed to `DefaultAgent` | Template contains `"SYS\n\nEXTRA"` (or equivalent separator) | §3 | — |
| MD-U-33 | `staff.system_prompt` empty/null: `system_template` unchanged | Config has `system_template="SYS"`; staff has `system_prompt=null` | Inspect template | Template is exactly `"SYS"` | §3 | — |
| MD-U-34 | `environment.type` is forced to `"local"` regardless of config | Config sets `environment.type=docker` | Inspect environment passed to `DefaultAgent` | `LocalEnvironment` is used | §3 | — |
| MD-U-35 | `environment.cwd` is forced to `<worktree>` | Config sets `environment.cwd="/tmp"` | Inspect `LocalEnvironment` | `cwd = <worktree path>` | §3 | — |

---

### Area 8: Agent — Session New / Resume

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-36 | New session calls `DefaultAgent.run()` and saves trajectory to correct path | `is_new_session=true`; no existing file | Run `_run_mr_deed` with deterministic model that submits | Trajectory file created at `/workspace/sessions/mr-deed/<session_id>.traj.json` | §5 | — |
| MD-U-37 | Resume session calls `DefaultAgent.from_trajectory().continue_run()` | `is_new_session=false`; trajectory file exists | Run `_run_mr_deed` | `from_trajectory` called with correct path; `continue_run` called; new turn appended to messages | §5 | — |
| MD-U-38 | `is_new_session=false` with missing trajectory file treated as new session | `is_new_session=false`; no file at expected path | Run `_run_mr_deed` | `DefaultAgent.run()` called (not `from_trajectory`); no error | §5 | — |
| MD-U-39 | Corrupted trajectory file on resume produces error reply, does not overwrite file | `is_new_session=false`; file exists but contains invalid JSON | Run `_run_mr_deed` | Error reply naming the file path; file on disk is not modified | §5 | — |
| MD-U-40 | Current config overrides applied on resume | Config changed between sessions | `from_trajectory(..., **current_config)` call | `current_config` kwargs passed to `from_trajectory`, not read from the saved file | §5 | — |
| MD-U-41 | System message not re-rendered on resume | Prior session has system message `S`; `system_template` changed | Resume session | Messages from prior session preserved; system message is still `S` (not re-rendered) | §5 | — |
| MD-U-42 | `output_path` passed so mr-deed saves after every step | New session | Inspect `DefaultAgent` constructor call | `output_path=<traj path>` argument present | §5 | — |
| MD-U-43 | Session trajectory round-trip: save then load preserves messages | Run a turn with deterministic model; then call `from_trajectory` on the saved file | Inspect loaded messages | All trajectory messages present in loaded agent | §5 | — |

---

### Area 9: Agent — Streaming Event Mapping

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-44 | `assistant` event emits chunk with type `mr_deed.assistant` | Deterministic model returns text message | Run turn; capture chunks published to `/chunk` | Chunk with `event.type = "mr_deed.assistant"` emitted; payload is the trajectory message | §6 | — |
| MD-U-45 | `action` event emits chunk with type `mr_deed.action` | Deterministic model returns tool call | Run turn; capture chunks | Chunk `event.type = "mr_deed.action"` before the action runs | §6 | — |
| MD-U-46 | `observation` event emits chunk with type `mr_deed.observation` | Action completes | Capture chunks | Chunk `event.type = "mr_deed.observation"` | §6 | — |
| MD-U-47 | `format_error` event emits chunk with type `mr_deed.format_error` | Model returns malformed action; mr-deed fires `format_error` | Capture chunks | Chunk `event.type = "mr_deed.format_error"` | §6 | — |
| MD-U-48 | `exit` event emits chunk with type `mr_deed.exit` | Any turn end | Capture chunks | Exactly one chunk `event.type = "mr_deed.exit"` per run | §6 | — |
| MD-U-49 | `system` event is NOT published | Turn start | Capture all chunks | No chunk with `event.type` containing `"system"` | §6 | — |
| MD-U-50 | `user` event is NOT published | Prompt submitted | Capture all chunks | No chunk with `event.type` containing `"user"` | §6 | — |
| MD-U-51 | Submission text becomes `last_response` in `/response` payload | Turn submits via `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` | Inspect `/response` payload | `last_response = <submission text>` | §6 | — |
| MD-U-52 | `transcript` in `/response` is full `messages` list | Run turn | Inspect `/response` payload | `transcript` equals `agent.messages` from the trajectory | §6 | — |
| MD-U-53 | MQTT publish failure inside `on_event` is caught, logged, and does not abort the turn | Monkeypatch MQTT publish to raise | Run full turn | Turn completes normally; `/response` sent; no exception escapes the worker | §6 | — |
| MD-U-54 | Exactly one chunk per event: N events produce N chunks (no duplicates, no drops) | Deterministic model: 2 assistant + 1 action + 1 observation + 1 exit | Count chunks | 5 chunks emitted | §6 | — |

---

### Area 10: Agent — Exit-Status and Failure Mapping

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-55 | `exit_status="Submitted"` → normal reply with `last_response` | Deterministic model submits | Run turn | Reply is a normal (non-error) reply | §7 | — |
| MD-U-56 | `exit_status="LimitsExceeded"` → error reply naming limit | Model hits cost/step limit; exit with `LimitsExceeded` | Run turn | Error reply text matches `"mr-deed hit a limit: <which> (<value>)"` | §7 | — |
| MD-U-57 | `exit_status="TimeExceeded"` → error reply | Turn times out | Run turn | Error reply `"mr-deed timed out after <N>s"` | §7 | — |
| MD-U-58 | `exit_status="RepeatedFormatError"` → error reply | Model repeatedly fails to produce a parseable action | Run turn | Error reply `"mr-deed could not produce a parseable action"` | §7 | — |
| MD-U-59 | `exit_status="Cancelled"` → error reply `"cancelled by user"` | Cancel token set | Run turn with cancel | Error reply `"cancelled by user"` | §7 | — |
| MD-U-60 | litellm / provider exception → error reply `"mr-deed model call failed: <summary>"` | Deterministic model raises a provider-style exception | Run turn | Error reply; exception does not escape the worker thread | §7 | — |
| MD-U-61 | Any other exception in `agent.run()` → error reply `"mr-deed crashed: <type>"` | Monkeypatch `agent.run()` to raise `RuntimeError("boom")` | Run turn | Error reply `"mr-deed crashed: RuntimeError"`; full traceback goes to logs; worker thread survives | §7 | — |
| MD-U-62 | Invalid / unloadable config → error reply naming source | `resolve_config` raises on config load | Run turn | Error reply `"mr-deed config '<source>' is invalid: <reason>"` | §7 | — |

---

### Area 11: Agent — Cancel Flow

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-63 | Running agent registered in `_running_agents[reply_message_id]` during run | Start `_run_mr_deed` | Inspect registry mid-run | Entry exists keyed by `reply_message_id` | §8 | — |
| MD-U-64 | `_running_agents` entry removed in `finally` after run completes | Run completes normally | Inspect registry after return | Key `reply_message_id` absent | §8 | — |
| MD-U-65 | `_running_agents` entry removed in `finally` even when run raises | Monkeypatch `agent.run()` to raise | Inspect registry after exception caught | Key absent | §8 | — |
| MD-U-66 | Cancel arriving during run: `agent.cancel()` called → run exits `Cancelled` | Two threads: one runs agent, one calls cancel after a delay | Run turn with slow deterministic model | `exit_status = "Cancelled"`; error reply sent | §8 | — |
| MD-U-67 | Cancel arriving before registration: recorded and applied right after registration | Pre-record cancel for `reply_message_id`; then start `_run_mr_deed` | Run turn | `agent.cancel()` called immediately after registration; run exits `Cancelled` | §8 | — |
| MD-U-68 | In-flight bash command killed on cancel | Turn running a slow bash command; cancel mid-command | Cancel | `LocalEnvironment.interrupt()` called; bash process group killed; exit `Cancelled` | §8 | — |
| MD-U-69 | Cancel does not leak into a subsequent turn on the same session | Cancel turn N; start turn N+1 on same session | Run turn N+1 | Turn N+1 runs to completion without `Cancelled` exit | §8 | — |

---

### Area 12: Per-Hop Environment Transport (§9)

One test per arrow in the chain per the 2026-08-16 lesson. All unit-level; no provider keys.

| ID | Case | Hop tested | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-70 | `test_process_prompt_mr_deed_sets_orch_env` | Payload → thread-local `_prompt_orch_env` | Publish a simulated mr-deed prompt payload; inspect `_prompt_orch_env` on the worker thread | `DISPATCH_TOKEN`, `TASK_DEPTH`, `TOPIC_ID`, `AGENT_NAME`, `PROMPT_MESSAGE_ID`, `WORKSPACE_ID`, `MASTER_URL` all set correctly on the thread-local | §9 | — |
| MD-U-71 | `test_run_mr_deed_builds_env_dict_from_thread_local` | Thread-local → `_run_mr_deed` local dict | Patch `_prompt_orch_env` with known values; call `_run_mr_deed` | Local env dict passed to runner contains the same values | §9 | — |
| MD-U-72 | `test_run_mr_deed_passes_env_to_local_environment` | Runner local dict → `LocalEnvironment(env=...)` | Inspect `LocalEnvironment` constructor call inside `_run_mr_deed` | `env` kwarg contains all seven per-turn keys with correct values | §9 | — |
| MD-U-73 | `test_bash_sees_dispatch_token_in_env` | `LocalEnvironment` → bash subprocess | Deterministic model issues `bash` command `echo $DISPATCH_TOKEN`; capture observation | Observation output matches the expected `DISPATCH_TOKEN` value | §9 | — |
| MD-U-74 | `test_host_tool_handler_reads_env_dict_not_os_environ` | Runner → host-tool handler (P2 handler; stub for P1) | Stub handler that reads `DISPATCH_TOKEN` from the env dict; confirm it does not read `os.environ` | Handler receives value from `LocalEnvironment.env`, not `os.environ` | §9 | — |
| MD-U-75 | None of the seven per-turn keys appear in `os.environ` after a turn | Run a full turn | Inspect `os.environ` | None of the seven keys present (or have unchanged pre-existing values if already set) | §9 | — |

---

### Area 13: Concurrency Isolation

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-76 | `test_two_mr_deed_turns_in_one_process_do_not_cross_contaminate` | Two payload dicts with distinct `DISPATCH_TOKEN` values | Submit both to the `agent-llm` pool simultaneously with deterministic models that capture their env | Each turn's model/handler sees only its own `DISPATCH_TOKEN`; no value from the other turn | §9 (concurrency) | — |
| MD-U-77 | Two concurrent sessions write to distinct trajectory files | Two turns with different `session_id` | Run concurrently | Each file written independently; no interleaving or path collision | §5 | — |

---

### Area 14: Backward Compatibility

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-U-78 | `claude-code` adapter path unchanged | Staff with `adapter="claude-code"` | Dispatch | `_run_claude` called; `_run_mr_deed` not reached | §1, backward compat | — |
| MD-U-79 | `codex` adapter path unchanged | Staff with `adapter="codex"` | Dispatch | `_run_codex` called; `_run_mr_deed` not reached | §1, backward compat | — |
| MD-U-80 | Staff without `agent_config` column (pre-migration) continues to dispatch without error | Simulate pre-migration staff object with no `agent_config` attribute | Dispatch | Dispatch succeeds; `agent_config` treated as `null` in payload | §1, backward compat | — |
| MD-U-81 | Payload logging does not leak `agent_config` contents | Payload with `agent_config` containing `model_kwargs: {api_key: sk-test}` | Trigger the scrubbed payload log | Logged payload replaces `agent_config` with a size marker (e.g. `<agent_config: N bytes>`); `sk-test` appears nowhere in logs; `dispatch_token` still scrubbed as before | §1 | — |

---

### Area 15: Frontend — Staff Forms

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-UAT-01 | `mr-deed` option present in adapter dropdown (`Settings.vue`) | Dev or staging env | Open Settings → create/edit global staff | Adapter dropdown contains `mr-deed` | §1 touch-points | needs-human |
| MD-UAT-02 | `mr-deed` option present in adapter dropdown (`WorkspaceDetail.vue`) | Dev or staging env | Open Workspace → Staffs → create/edit staff | Adapter dropdown contains `mr-deed` | §1 touch-points | needs-human |
| MD-UAT-03 | `agent_config` YAML textarea shown when adapter = `mr-deed` | Staff form open | Select `mr-deed` as adapter | YAML textarea appears | §1 touch-points | needs-human |
| MD-UAT-04 | `agent_config` YAML textarea hidden when adapter != `mr-deed` | Staff form open, adapter = `claude-code` | Observe form | No `agent_config` textarea visible | §1 touch-points | needs-human |
| MD-UAT-05 | `agent_config` value saved and reloaded in staff form | Enter YAML in textarea; save | Reopen staff edit form | Textarea shows the same YAML | §1 touch-points | needs-human |

---

### Area 16: Frontend — TopicChat Chunk Classifier

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-UAT-06 | `mr_deed.assistant` chunk displays as text bubble | mr-deed staff running in topic | Send message; observe reply | Text content of `mr_deed.assistant` event rendered as a text/thinking bubble | §6 | needs-human |
| MD-UAT-07 | `mr_deed.action` chunk displays as tool-use block | mr-deed staff issues a bash command | Observe action chunk | Action block rendered (tool_use style) | §6 | needs-human |
| MD-UAT-08 | `mr_deed.observation` chunk displays folded | Bash observation received | Observe chunk | Observation rendered in folded/collapsible style | §6 | needs-human |
| MD-UAT-09 | `mr_deed.format_error` chunk displays as parse_warning | Model produces a format error | Observe chunk | Warning-style indicator shown | §6 | needs-human |
| MD-UAT-10 | `mr_deed.exit` chunk is hidden (not rendered) | Turn ends | Observe chunks | No visible element for `mr_deed.exit` in the message thread | §6 | needs-human |
| MD-UAT-11 | Unknown `mr_deed.*` type maps to `hidden` (classifier fallback) | Inject a synthetic chunk with type `mr_deed.future_type` | Observe | No error; chunk silently hidden | §6 | automated |

MD-UAT-11 can be asserted by calling the staging `/chunk` WebSocket endpoint with a fabricated event and observing no JavaScript error and no rendered element via API response checks.

---

### Area 17: Integration — Adapter Round-Trip through MQTT

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-I-01 | Dispatch payload with `adapter="mr-deed"` routes to `_run_mr_deed` | Agent container running on dev env; deterministic model configured | POST message to topic with mr-deed staff | MQTT `/prompt` payload received; `_run_mr_deed` called (verifiable via log or stub); no error | §1 | automated |
| MD-I-02 | `session_id` in payload matches trajectory file path on volume | New session | Run turn; inspect volume | File at `/workspace/sessions/mr-deed/<session_id>.traj.json` exists | §5 | automated |
| MD-I-03 | `/response` payload received for completed turn | Turn runs to submission | Wait for WebSocket `/response` | `last_response` non-empty; `session_id` present | §6 | automated |
| MD-I-04 | Error reply received for `LimitsExceeded` exit | Config step limit = 1; deterministic model does not submit in 1 step | Run turn | Error reply `"mr-deed hit a limit: ..."` received via WebSocket | §7 | automated |

---

### Area 18: UAT — End-to-End Scenarios

| ID | Case | Preconditions | Steps | Expected | Design ref | UAT kind |
|---|---|---|---|---|---|---|
| MD-UAT-12 | mr-deed staff completes a bash-only task end-to-end | Staging env; mr-deed staff configured with a real model and API key; `agent_config` uses built-in `default` config | Send message `"Run: echo hello-world and submit the result"` | Normal reply containing `"hello-world"`; streaming chunks visible in UI | §6, §7 | needs-human |
| MD-UAT-13 | Session resume after agent-process restart | Prior turn produced a trajectory file; restart the agent container (`sre`); send a follow-up message (`is_new_session=false`) | Observe reply | Reply continues from prior context (references prior turn); trajectory file updated | §5 | needs-human |
| MD-UAT-14 | Cancel mid-turn via UI | mr-deed staff running a slow bash command | Click Cancel in the UI | Error reply `"cancelled by user"` received; no hanging MQTT messages | §8 | needs-human |
| MD-UAT-15 | Non-mapping `agent_config` rejected at save (UI) | Staff form with adapter `mr-deed` | Enter `agent_config: "a scalar string"` and save | Form shows validation error (422); staff not saved | §3 | needs-human |
| MD-UAT-16 | Missing API key produces a model-call error reply | mr-deed staff with no provider API key set in workspace config vars | Send message | Error reply `"mr-deed model call failed: <summary>"`; no crash visible | §7 | automated (assert error reply shape via API) |
| MD-UAT-17 | Staffs without `agent_config` (claude-code / codex) are unaffected | Existing non-mr-deed staff | Send message to that staff | Reply arrives normally; no change in behaviour | Backward compat | automated |

---

## Edge Cases

| Edge case | Test ID(s) |
|---|---|
| `is_new_session=false` but file missing → treated as new | MD-U-38 |
| Corrupted / unreadable trajectory → error reply, no overwrite | MD-U-39 |
| Cancel arriving before agent registered → applied right after registration | MD-U-67 |
| Cancel does not bleed into next turn | MD-U-69 |
| MQTT publish failure inside `on_event` → logged, turn continues | MD-U-53 |
| Unknown config name with no tier-0 YAML | MD-U-29 |
| Config that passes master 422 check but mr-deed rejects at load | MD-U-30 |
| `staff.agent` empty/null → default config name `"default"` | MD-U-27, MD-U-28 |
| System message not re-rendered on resume | MD-U-41 |
| Concurrent turns writing to different trajectory files | MD-U-77 |
| `mr_deed.exit` chunk hidden (classifier fallback for unknown types) | MD-UAT-10, MD-UAT-11 |
| Any exception in `agent.run()` → isolated to worker thread | MD-U-61 |

---

## Failure Modes

| Failure mode | Expected behaviour | Test ID(s) |
|---|---|---|
| `agent_config` is a YAML scalar or list | 422 on save | MD-U-13, MD-U-14 |
| `agent_config` is syntactically invalid YAML | 422 on save | MD-U-15 |
| Config mr-deed cannot load → named error reply | Error reply `"mr-deed config '...' is invalid: ..."` | MD-U-62 |
| Unknown config name and no fallback | Error reply | MD-U-29 |
| litellm / provider exception | Error reply `"mr-deed model call failed: ..."` | MD-U-60, MD-UAT-16 |
| Unhandled exception in `agent.run()` | Error reply `"mr-deed crashed: <type>"`; worker thread survives | MD-U-61 |
| LimitsExceeded | Named error reply | MD-U-56, MD-I-04 |
| TimeExceeded | Named error reply | MD-U-57 |
| RepeatedFormatError | Named error reply | MD-U-58 |
| Cancelled | Error reply `"cancelled by user"` | MD-U-59, MD-UAT-14 |
| Cancel before registration | Applied immediately after registration; exits Cancelled | MD-U-67 |
| Corrupted trajectory on resume | Error reply naming file; file not overwritten | MD-U-39 |
| MQTT publish failure in `on_event` | Logged; turn not aborted | MD-U-53 |
| Missing API key | Error reply from model call failure | MD-UAT-16 |

---

## Non-Functional Requirements

| Requirement | Verification approach |
|---|---|
| Per-turn env values never reach `os.environ` | MD-U-75, MD-U-70 through MD-U-74 |
| Importing the runner leaves `os.environ` and `sys.modules[mr_deed.run]` untouched | MD-U-02, MD-U-03 |
| Two concurrent turns do not cross-contaminate per-turn env | MD-U-76 |
| Exceptions in `agent.run()` never escape the worker thread | MD-U-60, MD-U-61, MD-U-53 |
| `agent_config` migration is additive (no existing rows deleted, no existing columns altered) | MD-U-06, MD-U-07 |
| `claude-code` and `codex` dispatch paths are unmodified | MD-U-78, MD-U-79, MD-UAT-17 |
| `mr-deed @ git+...@v0.1.0` exact pin in `requirements.txt` | MD-U-01 |
| Master process does not import `mr_deed` | MD-U-16 |

---

## Out of Scope for This Test Plan

- Host tool (`delegate_task`, `ask_sender`, `submit_result`, `accept_result`, `reject_result`, `give_up_task`, notes.*) unit and integration tests — P2 test plan.
- `orchestration_tools_for` gating function tests — P2 test plan.
- Per-turn context refactor of orchestrate/notes MCP servers — P2 test plan.
- Tier-1 config resolution (`.prj_assistant/mr-deed/`) — P3 test plan.
- Session compaction / context-growth limits — post-P1 ADR.
- Image size measurement — SRE + engineer task.
- Cross-repo mr-deed upgrade validation — separate PR per upgrade.
- UAT execution against the live staging singleton — deferred to step 15 of the feature workflow.
