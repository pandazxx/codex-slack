# Design: Mr-deed agent adapter

**Status:** accepted (ADR-0018)
**Author:** architect
**Date:** 2026-10-09 (updated 2026-10-14 for mr-deed `v0.1.0`)
**Related ADRs:** [ADR-0018](../decisions/0018-mr-deed-agent-adapter.md);
builds on ADR-0001 (agent provisioning), ADR-0014 (Codex adapter),
ADR-0017 (orchestration protocol), ADR-0009 (staff system), ADR-0012
(streaming reply), ADR-0015 (notes feature).
**Issue:** [#265](https://github.com/pandazxx/codex-slack/issues/265)
(folds in [#256](https://github.com/pandazxx/codex-slack/issues/256) at P2).
**Upstream repo:** [`pandazxx/mr-deed`](https://github.com/pandazxx/mr-deed).

## Context

The platform runs two adapters today: `claude-code` and `codex`. Both are
CLI subprocesses that stream JSONL on stdout, keep their own session
state, and discover tools through MCP config files. We want to add
`mr-deed` — a fork of mini-swe-agent v2 (package `mr_deed`, dist
`mr-deed`) owned by the same project-owner — as a third adapter.
The intent is a side-kick / orchestrator / assistant that can also
handle simple tasks end-to-end, with the latitude to iterate on agent
flow and tool-call behaviour in-tree because both repositories are
co-owned.

The gaps vs. the two existing adapters were: in-process only, no
sessions, `bash` as the only tool, no event hooks or cancel, and import-
time `.env` loading into `os.environ`. Following the boundary rule, they
were filed as mr-deed issues #6–#11 and shipped in **mr-deed `v0.1.0`**
(PR #13, `3834d02`). This design is written against that release; the
API is summarised in ADR-0018 (Context) and used directly below. There
are no codex-slack-side subclasses or patches of mr-deed.

The design converged in issue #265 (comments from 2026-10-04 through
2026-10-08). This document records the settled decisions and fills in
mechanism detail. Open items that are genuinely unresolved are called
out in §Open Questions.

## Goals

- **New adapter `mr-deed`** behind the same dispatch envelope used by
  `claude-code` and `codex`. `staff_sessions` and session scope are
  unchanged. The staff record and dispatch payload gain one field,
  `agent_config` (§3). UI changes: a YAML editor for `agent_config` in
  the staff form, and classifier branches for `mr_deed.*` chunk events
  (§6).
- **In-process execution** on the existing `agent-llm` ThreadPoolExecutor
  (4 workers) in `src/agent/mqtt_loop.py`. No child process per turn.
- **Per-turn env isolation.** `DISPATCH_TOKEN`, `TASK_DEPTH`, `TOPIC_ID`,
  `AGENT_NAME`, `PROMPT_MESSAGE_ID`, `WORKSPACE_ID`, `MASTER_URL` ride
  on `LocalEnvironment(env=...)` and never on `os.environ`.
- **Config resolution** staff `agent_config` (codex-slack config, edited
  in the web GUI) → worktree → shipped → mr-deed built-ins, first match
  wins. Runner always forces `environment.type=local,
  cwd=<worktree>`.
- **Session resume.** Persist trajectory per `session_id` on a volume;
  resume on `is_new_session=false` by appending a new user message
  rendered through `continue_template`.
- **Streaming.** Each agent event becomes a `/chunk` frame; the
  submission becomes `last_response`. `LimitsExceeded`, `TimeExceeded`,
  `RepeatedFormatError`, `Cancelled` map to named error replies.
- **Cancel.** Per-reply cancel token checked between steps; in-flight
  bash process group killed on cancel.
- **Native host tools** (P2). The tool set per turn is `[bash] +
  <codex-slack tools gated by TASK_DEPTH and role per ADR-0017 T1>`;
  handlers call master's HTTP endpoints directly (reusing the client
  code already in `src/orchestrate_mcp/` and `src/notes_mcp/`), no MCP
  hop. Master still validates every call.
- **Credentials.** Reuse the config-var → container env mechanism;
  litellm reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / etc.
- **Clean cross-repo boundary.** Interface requirements are mr-deed
  issues and consumed through a pinned tag (`v0.1.0`); no codex-slack-side
  subclasses of mr-deed.
- **Generic agent interface.** `hermes` is an experiment; no special
  casing.

## Non-Goals

- **Memory management.** Out of scope for this ADR / design. Session
  resume is the foundation; agent-memory lands in a later ADR.
- **History compaction.** Linear history, no compaction in v1 — a
  documented limitation. Long sessions hit context/cost ceilings.
- **Codex OAuth or Claude subscription login.** Litellm cannot use
  them. API-key billing only.
- **Text-based mini configs with host tools.** Host tools are tool-
  call-mode only (ADR-0017 T1 depends on structured tool calls).
- **Alternate `environment.type`s** (docker, modal, …). The runner
  always forces `local` so a repo config cannot break out of the
  worktree.
- **hermes-specific plumbing.** It is one config in mr-deed, not a
  codex-slack concept.

## Design

### 1. Component layout and one-turn flow

```mermaid
sequenceDiagram
    participant U as User / dispatcher
    participant M as Master
    participant D as dispatch_to_staff
    participant MQTT
    participant AL as agent-llm pool
    participant R as _run_mr_deed
    participant SA as DefaultAgent
    participant LE as LocalEnvironment
    participant LLM as litellm

    U->>M: POST /messages (or event dispatch)
    M->>D: dispatch(staff=mr-deed-xxx, payload)
    D->>MQTT: publish /prompt (adapter='mr-deed', per-turn env)
    MQTT->>AL: _process_prompt(payload)
    AL->>R: _run_mr_deed(payload)
    R->>R: resolve config (worktree → shipped → built-in)
    R->>SA: new turn: DefaultAgent(model, LE(cwd=worktree, env=per-turn), tools, on_event, **config).run(prompt)
    R->>SA: resume: DefaultAgent.from_trajectory(path, model, LE, tools=..., on_event=...).continue_run(prompt)
    SA->>LLM: completion(messages, tools=[bash, ...codex-slack tools])
    LLM-->>SA: assistant message
    SA->>AL: on_event("assistant") → /chunk
    SA->>LE: execute(bash cmd) [or host-tool handler]
    LE-->>SA: observation
    SA->>AL: on_event("action" / "observation") → /chunk
    SA->>LLM: completion(messages + observation)
    Note over SA,LLM: Loop until submission, limit, or cancel
    SA->>AL: on_event("exit"); submission → last_response; save() trajectory
    AL->>MQTT: publish /response (with transcript)
    MQTT->>M: _save_agent_response
    M-->>U: WS broadcast reply
```

Touch-points in the existing code:

| File | Change |
|---|---|
| `src/master/staffs.py` | `_VALID_ADAPTERS.add("mr-deed")`; schema docs for valid values |
| `src/master/db.py` | Additive migration: nullable `staffs.agent_config TEXT` |
| `src/master/staffs.py` | `agent_config` on `StaffIn`/`StaffOut`; on save, reject YAML that doesn't parse to a mapping (syntax check only — master doesn't import `mr_deed`, §11) |
| `src/master/dispatch.py` | Add `agent_config` to the prompt payload from the resolved staff |
| `frontend/src/views/Settings.vue`, `WorkspaceDetail.vue` | YAML textarea for `agent_config` in the staff form, shown when `adapter = mr-deed` |
| `src/agent/mqtt_loop.py::_process_prompt` (~line 636) | `elif adapter == "mr-deed": _run_mr_deed(...)` |
| `src/agent/mqtt_loop.py` | New `_run_mr_deed` + streaming helpers next to `_run_claude` / `_run_codex` |
| `src/agent/mr_deed_runner.py` (new) | Config resolution, agent construction/resume, `on_event` → chunk publisher, running-agent registry for cancel. Uses mr-deed's public API only |
| `src/orchestrate_mcp/`, `src/notes_mcp/` (P2) | Extract call-time client functions taking a per-turn context; MCP servers become thin wrappers |
| `src/master/orchestration.py` or a shared module (P2) | `orchestration_tools_for(depth, max_depth)` — single gating function used by the MCP server and mr-deed |
| `frontend/src/views/TopicChat.vue` | Classifier branches for `mr_deed.*` event types (§6) |
| `requirements.txt` | Add `mr-deed @ git+https://github.com/pandazxx/mr-deed@v0.1.0` (§11) |

### 1a. Where each piece runs

| Piece | Runs in | Notes |
|---|---|---|
| Staff resolution, tier-0 `agent_config` lookup | master | Existing staff cascade |
| Dispatch, task state, orchestration/notes endpoints | master | Unchanged (ADR-0017) |
| mr-deed library (`mr_deed`) | agent container, `mqtt_loop` process | Installed in the shared image (§11); only the agent process imports it |
| Tier 1–3 config resolution | agent container | Needs the worktree and the imported `mr_deed` built-in configs |
| Model calls (litellm) | agent container → provider API | Provider keys from container env (§12) |
| `bash` tool | agent container, `LocalEnvironment` subprocess, `cwd=<topic worktree>` | Same place claude/codex run commands today |
| Host-tool handlers | agent container, same thread as the agent | Call master over HTTP |

### 2. Adapter dispatch mapping

The staff → runner mapping. Master changes: the enum and one new payload
field, `agent_config`.

| Payload field | Source | Mr-deed binding |
|---|---|---|
| `adapter` | `staff.adapter` | Selects `_run_mr_deed` |
| `subagent` | `staff.agent` | Config name for tiers 1–3 (§3) |
| `agent_config` | `staff.agent_config` | Tier-0 YAML; used as-is when non-empty (§3) |
| `model` | `staff.model` | Overrides `model.model_name` |
| `system_prompt` | `staff.system_prompt` | Appended to `system_template` |
| `session_id` | `_get_staff_session()` | Trajectory file name (§5) |
| `is_new_session` | `_get_staff_session()` | `false` → load and resume |
| `session_scope` | `staff.session_scope` | Already honoured by master; mr-deed just sees the resolved `session_id` |
| `task_depth` | master task machinery | Chooses tool subset (ADR-0017 T1) |
| `dispatch_token` | master | Carried in per-turn env for host-tool handlers |

### 3. Config resolution

First match wins:

0. **`staff.agent_config`** — codex-slack config. Mr-deed YAML stored on
   the staff record and edited in the web GUI staff form. It is resolved
   by the existing staff cascade (topic → workspace → global, full
   override, ADR-0009 §2): a global staff carries the global config, and
   a workspace- or topic-scoped staff of the same name replaces it.
   Master puts the resolved YAML in the dispatch payload.
1. `<worktree>/.prj_assistant/mr-deed/<staff.agent>.yaml` (arrives in P3).
2. `/opt/codex-slack/config/mr-deed/<staff.agent>.yaml` (shipped with
   the agent image; checked into this repo under `config/mr-deed/`).
3. Mr-deed built-ins (`default`, `mini`, `hermes` and friends).

Tier 0 is resolved on master, where staffs live. Tiers 1–3 are resolved
on the agent in `src/agent/mr_deed_runner.py::resolve_config(agent_config,
worktree, name)`, since only the agent container has the worktree and
imports `mr_deed`. Master only syntax-checks `agent_config` on save
(must parse to a YAML mapping); a config mr-deed rejects at load time
becomes a named error reply on the turn (§7).

`agent_config` is stored in its own staff column, not in the `config`
table: `config` values are injected into the agent container as env
vars, which suits neither multi-line YAML nor per-staff scope.

Once a YAML is picked, the runner applies overrides:

```
model.model_name    ← staff.model if present
system_template     ← system_template + "\n\n" + staff.system_prompt (if present)
environment.type    ← "local"           # forced
environment.cwd     ← <worktree>        # forced
environment.env     ← <per-turn env dict>   # from _prompt_orch_env + master-url
```

Project configs (P3) are trusted like `CLAUDE.md`: anything committed to
the repo is treated as part of the code. The forced `environment.type=
local` means a repo config cannot switch execution to docker or any other
environment, so the blast radius of a malicious repo config is the same
as a malicious script in the repo.

### 4. Tool gating (ADR-0017 T1 for mr-deed)

Each turn builds a tool list from `TASK_DEPTH` and the turn's role.
`bash` is always present and primary. Host tools are additive and
registered as `mr_deed.Tool(name, description, parameters, handler)`
objects passed via `DefaultAgent(tools=...)`. mr-deed validates names and
required arguments, calls `handler(arguments)`, and turns a handler
exception into an observation with `returncode -1`. A failing master
call is therefore something the model can see and react to, not a
crashed turn.

The rule set is exactly the one `src/orchestrate_mcp/server.py` applies to
Claude/Codex today. P2 extracts it into one shared function
(`orchestration_tools_for(depth, max_depth)`) so the MCP server and the
mr-deed runner cannot drift:

| Tool | Present when | Notes |
|---|---|---|
| `bash` | always | Primary tool; unchanged |
| `ask_sender` | always | Depth 0 → asks the user; depth ≥ 1 → asks the dispatcher |
| `delegate_task` | `TASK_DEPTH < MAX_DELEGATION_DEPTH` | Default max = 1 → depth-0 turns only |
| `submit_result` | `TASK_DEPTH ≥ 1` | Assignee of a delegated task |
| `answer_question`, `accept_result` | `TASK_DEPTH == 0` | Dispatcher turns |
| `reject_result`, `give_up_task` | `TASK_DEPTH == 0` | Added when ADR-0017 phase (c) ships them; not in the MCP server yet |
| `notes.*` | always | Workspace + topic notes |

Text-based mini configs (no tool-call mode) get `bash` only.

Handlers are plain Python callables that reuse the HTTP client code of
the orchestrate/notes MCP servers (`src/orchestrate_mcp/` and
`src/notes_mcp/`). **That code cannot be imported as-is:**
`orchestrate_mcp/server.py` reads `MASTER_URL`, `TOPIC_ID`,
`DISPATCH_TOKEN`, `TASK_DEPTH`, … into module-level globals at import
time (safe today only because each CLI turn spawns a fresh MCP process).
In one long-lived agent process those globals would freeze the first
turn's values for every later turn — the same failure as the 2026-05-18
`alwaysLoad` lesson. P2 therefore refactors the client into call-time
functions that take an explicit per-turn context; the MCP servers become
thin wrappers that build that context from their own env. Each handler:

1. Reads `DISPATCH_TOKEN` / `TOPIC_ID` / `WORKSPACE_ID` / `MASTER_URL`
   from the `LocalEnvironment.env` dict (not `os.environ`).
2. Calls master's HTTP endpoint directly (no MCP hop).
3. Returns the structured response, which mr-deed renders as an
   observation message.

Master still validates every call server-side against the
communication-matrix and the task-state machine (ADR-0017 §1 and §3).
A fabricated call is rejected at the API, not at the client.

### 5. Session file layout and resume

```
/workspace/sessions/mr-deed/<session_id>.traj.json
```

`<session_id>` is the dispatch payload's `session_id` (already scoped by
`staff.session_scope` on master; the adapter stays agnostic). The file is
mr-deed's own `agent.save()` trajectory format, so no codex-slack schema
needs maintaining:

```json
{
  "info": {
    "model_stats": {"instance_cost": 0.0, "api_calls": 0},
    "config": {"agent": {"system_template": "...", "continue_template": "{{ task }}", "...": "..."}},
    "exit_status": "Submitted",
    "submission": "..."
  },
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "<rendered instance_template>"},
    {"role": "assistant", "...": "..."},
    {"role": "tool", "tool_call_id": "...", "content": "..."},
    {"role": "exit", "...": "..."},
    {"role": "user", "content": "<rendered continue_template>"}
  ],
  "trajectory_format": "..."
}
```

| Case | Runner call |
|---|---|
| `is_new_session=true` (or file missing) | `DefaultAgent(model, env, tools=..., on_event=..., output_path=<file>, **config).run(prompt)`; system + `instance_template` rendered once |
| `is_new_session=false` | `DefaultAgent.from_trajectory(<file>, model, env, tools=..., on_event=..., **config).continue_run(prompt)` |

Notes:

- The currently resolved config is passed as overrides to
  `from_trajectory`, so GUI or file edits apply to the next turn (limits,
  `continue_template`, observation templates). The **system message is
  not re-rendered**: it was fixed when the session started. A changed
  `system_template` or `staff.system_prompt` takes effect in new
  sessions only.
- `tools` are rebuilt every turn, so depth/role gating (§4) is always
  per turn, even within one session.
- Step/cost/wall-time limits reset per `run`/`continue_run`, so mr-deed
  config limits are per turn. `total_cost`/`total_n_calls` (saved as
  `model_stats`) span the session.
- `exit` records of earlier turns stay in the file as turn boundaries but
  are filtered out of the model's context by mr-deed.
- `output_path` makes mr-deed save after every step, so a crash mid-turn
  still leaves a resumable file.
- No compaction: the file grows linearly (Open Questions). The
  `/workspace/sessions/` volume already holds the claude sessions for
  workspace/global scope.

### 6. Streaming event → chunk mapping

The runner passes `on_event=publish` to the agent. mr-deed calls it
synchronously, on the agent's worker thread, for every trajectory message
and for every action before it runs. `publish` wraps the payload as an
`event` on the existing `/chunk` pipeline (ADR-0012), the same transport
`_stream_codex_once` uses. mr-deed requires that `on_event` handlers
never raise, so `publish` catches and logs its own failures (an MQTT
hiccup drops a chunk, not the turn). The final reply still travels on
`/response`.

The frontend classifier in `TopicChat.vue` dispatches on `event.type`
and maps unknown types to `hidden`, so mr-deed events get their own
`mr_deed.*` types plus classifier branches onto the existing display
kinds. Native event types keep the chunk payload a faithful copy of the
trajectory message instead of forging claude/codex shapes.

| `on_event` kind | Chunk `event.type` | Classifier → display kind |
|---|---|---|
| `system`, `user` | — | not published (the prompt is already in the topic) |
| `assistant` (text / reasoning) | `mr_deed.assistant` | `text` (reasoning → `thinking`) |
| `action` (bash or host tool) | `mr_deed.action` | `tool_use` |
| `observation` (bash or host-tool result) | `mr_deed.observation` | `folded` |
| `format_error` | `mr_deed.format_error` | `parse_warning` |
| `exit` (submission, limit, cancel, error) | `mr_deed.exit` | `hidden` (the reply itself carries the outcome) |

The `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` submission text becomes
`last_response` in the `/response` payload.

`transcript` in the `/response` is the full `messages` list from the
trajectory file.

### 7. Exit-status and failure mapping

| Mr-deed outcome | Reply type | Error text |
|---|---|---|
| `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` | normal reply | — |
| `LimitsExceeded` (cost / step) | error reply | `"mr-deed hit a limit: <which> (<value>)"` |
| `TimeExceeded` (wall) | error reply | `"mr-deed timed out after <N>s"` |
| `RepeatedFormatError` | error reply | `"mr-deed could not produce a parseable action"` |
| `Cancelled` (our cancel token) | error reply | `"cancelled by user"` |
| Litellm / provider exception | error reply | `"mr-deed model call failed: <summary>"` |
| Any other exception in `agent.run()` | error reply | `"mr-deed crashed: <type>"` (full traceback goes to logs) |
| Resolved config fails to load or validate | error reply | `"mr-deed config '<source>' is invalid: <reason>"` |

The runner catches every exception around `agent.run()` so a litellm
failure stays confined to its worker thread (crash isolation).

### 8. Cancel flow

```mermaid
sequenceDiagram
    participant UI
    participant M as Master
    participant ML as mqtt_loop (cancel handler)
    participant SA as DefaultAgent (agent-llm thread)
    participant LE as LocalEnvironment
    participant BA as bash pgroup

    UI->>M: POST /cancel reply_message_id=R
    M->>ML: /cancel keyed by R
    ML->>SA: _running_agents[R].cancel()
    SA->>LE: interrupt()
    alt command running
        LE->>BA: killpg(SIGKILL)
    else no command yet
        LE->>LE: pending — kill the next command as soon as it spawns
    end
    SA->>SA: next query/action → exit_status "Cancelled"
    SA->>ML: on_event("exit") ; run returns
    ML->>M: publish /response (error reply "cancelled")
```

The existing cancel mechanism (`_active_procs` + `_active_procs_lock`)
is subprocess-shaped. For mr-deed the runner registers the live agent in
`_running_agents[reply_message_id]` (same lock discipline, removed in a
`finally` when the turn ends). The cancel handler calls
`agent.cancel()`, which is thread-safe and does both halves: it sets the
agent's cancel event (checked before every model query and action) and
interrupts the in-flight or about-to-start bash command. A cancel that
arrives before the agent is registered (while the trajectory is still
loading) is recorded and applied right after registration. mr-deed
resets pending interrupts at the start of each run, so a cancel never
leaks into a later turn.

### 9. Per-turn env hygiene (lesson 2026-08-16)

The lesson is: when a value crosses process/thread boundaries, write at
least one test per hop. For mr-deed the hops are:

```
master dispatch payload
    → MQTT prompt
    → _process_prompt (thread-local _prompt_orch_env.env)
    → _run_mr_deed (local dict)
    → LocalEnvironment(env=...)  [bash sees it here]
    → host-tool handler (reads env dict, not os.environ)
```

**Each arrow is a test.** Specifically:

| Hop | Test |
|---|---|
| Payload → thread-local | `test_process_prompt_mr_deed_sets_orch_env` |
| Thread-local → runner | `test_run_mr_deed_builds_env_dict_from_thread_local` |
| Runner → LocalEnvironment | `test_run_mr_deed_passes_env_to_local_environment` |
| LocalEnvironment → bash | `test_bash_sees_dispatch_token_in_env` |
| Runner → host-tool handler | `test_host_tool_handler_reads_env_dict_not_os_environ` |

Concurrency: `test_two_mr_deed_turns_in_one_process_do_not_cross_
contaminate` runs two staffs with distinct `DISPATCH_TOKEN` values on
the pool in parallel and asserts each handler sees its own token.

### 10. mr-deed interface consumed

No codex-slack-side stopgaps. The earlier plan (session subclass,
`add_messages` override, cancel/env subclass, dotenv guard, litellm model
subclass) is dropped because each piece shipped in mr-deed `v0.1.0`:

| Need | mr-deed issue | `v0.1.0` API used |
|---|---|---|
| Session resume | [#6](https://github.com/pandazxx/mr-deed/issues/6) | `from_trajectory`, `continue_run`, `continue_template`, `save()` |
| Streaming | [#7](https://github.com/pandazxx/mr-deed/issues/7) | `on_event(kind, payload)` |
| Cancel | [#8](https://github.com/pandazxx/mr-deed/issues/8) | `agent.cancel()`, `LocalEnvironment.interrupt()` / `reset_interrupt()` |
| Host tools | [#9](https://github.com/pandazxx/mr-deed/issues/9) | `mr_deed.Tool`, `DefaultAgent(tools=...)` |
| Embedding hygiene | [#10](https://github.com/pandazxx/mr-deed/issues/10) | side-effect-free `import mr_deed`; per-agent limits |
| Pinning | [#11](https://github.com/pandazxx/mr-deed/issues/11) | `v0.1.0` tag |

The runner must import only from `mr_deed` (agents, models,
environments, config), **never from `mr_deed.run`**: that package is the
CLI entry point and loads `.env` files into `os.environ` on import. A
unit test asserts that importing the runner leaves `os.environ`
untouched.

New needs found during implementation go back to mr-deed as issues
(boundary rule), not into local workarounds.

### 11. Packaging and image impact

```
# requirements.txt
mr-deed @ git+https://github.com/pandazxx/mr-deed@v0.1.0
```

By default master and agent containers run the **same image**
(`MASTER_AGENT_BASE_IMAGE` defaults to the master's image), all built
from `requirements.txt`. So mr-deed and its transitive **litellm**
dependency are installed in master too, but only the agent process
imports them. Master doesn't import `mr_deed`, keeping litellm out of the
master process. That's why `agent_config` validation on master stays a
YAML syntax check (§3). The image-size delta is measured in P1 and
recorded in the release notes; splitting agent-only dependencies into a
separate requirements file is an option if the delta matters (Open
Questions).

Upgrades are explicit tag bumps (`@v0.1.0` → `@v0.x.y`) in their own PR,
with the mr-deed changelog linked.

### 12. Credentials

The existing config-var → container env path (ADR-0010) is reused. The
sensitive config variables set at the workspace level — e.g.
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` — flow into
`os.environ` in the agent container (as today), and litellm picks them
up from there.

**What does not work:** Codex's OAuth `auth.json` and a Claude
subscription login are both outside litellm's authentication surface.
Mr-deed staffs require a real API key for whichever provider their
model targets. Documented in the user manual and in a system-prompt
footer for the shipped configs so operators get a clear error if the
key is missing.

### 13. Phasing

Project-owner swapped P2 and P3 so orchestration tools land before
project configs.

#### P1 — adapter + runner + streaming + resume + cancel

- `_VALID_ADAPTERS.add("mr-deed")` + `_run_mr_deed` + runner module.
- Config resolution: staff `agent_config` (DB column, payload field,
  GUI YAML editor) → shipped (`config/mr-deed/`) → built-in.
- Session persist / resume via `save()` + `from_trajectory` +
  `continue_run`.
- Streaming via `on_event`.
- Cancel via `agent.cancel()` and a running-agent registry.
- Pin `mr-deed @ ...@v0.1.0` in `requirements.txt`.
- mr-deed deps: all shipped in `v0.1.0`.

#### P2 — codex-slack host tools (#256 support)

- Register `delegate_task`, `ask_sender`, `answer_question`,
  `submit_result`, `accept_result`, `reject_result`, `give_up_task`,
  plus notes.*, as native in-process Python tools.
- Tool-list computation per turn per the gating table (§4).
- Handlers reuse the HTTP client code from `src/orchestrate_mcp/` and
  `src/notes_mcp/`; master still validates every call.
- Uses `mr_deed.Tool` / `DefaultAgent(tools=...)` from `v0.1.0`.

#### P3 — project configs under `.prj_assistant/mr-deed/`

- Config resolution picks up worktree-local YAMLs first.
- Trust model: repo content is trusted; `environment.type=local` stays
  forced.

### 14. Testing strategy

- **Unit tests** in `tests/agent/test_mr_deed_adapter.py` using a
  deterministic / fake model (no litellm) and a fake
  `LocalEnvironment`:
  - Adapter enum round-trips through master → MQTT → `_process_prompt`.
  - Config resolution: `agent_config` over worktree over shipped over
    built-in; an invalid `agent_config` is rejected on save (master) and
    a config mr-deed can't load becomes a named error reply (agent).
  - Staff cascade: a topic-scoped staff's `agent_config` replaces the
    global staff's (no field merge).
  - System-prompt override is appended, not replaced.
  - Streaming emits exactly one chunk per event in the right shape.
  - Exit-status mapping covers each exit status (`Submitted`,
    `LimitsExceeded`, `TimeExceeded`, `RepeatedFormatError`,
    `Cancelled`, uncaught exception).
  - Session trajectory load → append → save round-trip; `is_new_session
    =false` continues the loop with the prior messages.
  - Cancel: token flip at a step boundary raises `Cancelled`; bash
    pgroup is killed when a cancel fires mid-command.
- **Per-hop env-transport tests** (§9) — one per arrow in the chain.
  This is the lesson from the 2026-08-16 dispatch-token miss.
- **Concurrency test:** two `_run_mr_deed` calls on the pool with
  different per-turn env dicts prove that `GLOBAL_MODEL_STATS` and
  friends do not cross-contaminate.
- **Integration test (P2):** a mr-deed staff calls `delegate_task` via
  the host tool; master records the task row; the assignee is
  dispatched with the correct `task_depth`.
- **UAT:** a mr-deed staff executes a bash-only end-to-end task with
  streaming visible in the UI; after an agent-process restart, a
  follow-up turn resumes the trajectory.

## Alternatives Considered

### Child-process-per-turn runner

Keep the import-as-library shape but spawn a child process for each
`agent.run()` call, with JSONL on stdout mirroring `_stream_codex_once`.

Rejected on project-owner preference. The main attractions were trivial
cancel (kill PID) and `os.environ` isolation. In exchange we would lose
the in-process host-tool integration (would need IPC between
mr-deed and master's HTTP client) and pay an extra fork/exec per turn,
which defeats the primary driver — deep integration.

### Bash CLI shim for codex-slack tools

Instead of registering native Python tools in mr-deed, ship a bash CLI
(`orch delegate_task --staff engineer --goal ...`, `notes list_workspace
_notes ...`) that reads `DISPATCH_TOKEN` from env and calls master.
Mr-deed would use plain `bash` to call it.

Rejected (project-owner call). Zero mr-deed changes and reusable for
other bash-only agents, but it adds a separate binary to ship and
version, adds another env-transport hop to test, and defeats the
deep-integration intent. If a future bash-only adapter wants
orchestration, we can reconsider.

### MCP client inside mr-deed

Have mr-deed itself speak MCP to master, reusing the servers in
`src/orchestrate_mcp/` and `src/notes_mcp/`.

Rejected. It adds a transport layer we deliberately avoid for the other
adapters, needs another process (or in-process MCP implementation) to
manage, and reintroduces the dependency-pin fragility that already bit
us once with the `mcp` 2.0 release (lessons-learned 2026-08-14).

### Codex-style CLI wrapper

Treat mr-deed as a CLI binary and mirror ADR-0014's shape.

Rejected. Mr-deed's `mini` CLI is single-shot and exposes no tool or
flow hooks, and the CLI shape forces subprocess serialisation — the
opposite of the deep-integration driver.

### No session resume (fresh run per turn)

Simplest option: skip trajectory persistence and let every turn be a
cold run.

Rejected. It breaks the `staff_sessions` contract and makes mr-deed
behave visibly differently from the other adapters.

### Dedicated credential blob

Add a `MR_DEED_AUTH_JSON` sensitive config var mirroring
`CODEX_AUTH_JSON`.

Rejected. Litellm already reads provider-named env vars; adding a new
blob would be a different UX for no gain. API-key limitation is
documented.

### Treat `hermes` as a first-class concept

Give the adapter a `hermes` toggle or special path that uses mr-deed's
hermes supervisor.

Rejected. `hermes` is one YAML config inside mr-deed, experimental, and
intentionally shaped like any other config. The adapter stays generic.

## Open Questions

- [ ] **Shape of tier-0 `agent_config` (deferred to detailed design).**
      YAML stored on each staff record (current draft) vs. a named
      config library at global/workspace scope that staffs reference by
      name and several staffs can share (needs its own CRUD + UI).
      Raised in PR #267 review; owner deferred it to the detailed design
      phase.
- [ ] **Tier-0 merge semantics (deferred to detailed design).** First
      match wins with full replacement (current draft, consistent with
      ADR-0009 full override) vs. an overlay merged onto the named base
      config via mr-deed's `recursive_merge`, so one field can change
      without copying the templates. Owner deferred to the detailed
      design phase.
- [ ] **Session history growth and compaction.** Linear history will
      eventually hit model context and cost ceilings for long topics.
      Options: hard cap on messages per session, summarisation turn,
      sliding window, or require operators to start a new session.
      Owner: architect; schedule after P1 lands and we see real
      lengths.
- [ ] **Image size impact of litellm.** It's installed in the shared
      master/agent image (§11). Measure in P1 and decide whether to
      move agent-only dependencies into a separate requirements file /
      image layer if the delta is significant. Owner: sre + engineer.
- [ ] **Trust boundary for `.prj_assistant/mr-deed/` (P3).** We treat
      the directory like `CLAUDE.md` for now. Project-owner marked
      "revisit later" — scheduling that review once P3 is in use and
      we have a sense of what shape config injections take.
- [ ] **Host-tool failure UX.** When a host tool errors (e.g. master
      rejects a `delegate_task` as `cycle_detected`), the mr-deed turn
      sees it as an observation. Is the observation message enough for
      the model to recover, or do we want a system-prompt footer that
      lists expected tool errors? Decide after we see a few real
      failures.
- [ ] **Streaming granularity for very chatty turns.** Mr-deed emits
      an `assistant` message per step. For long sessions the chunk
      pipeline may need backpressure. Not urgent — the existing chunk
      pipeline is already battle-tested — but worth measuring once a
      P2 orchestrator staff is live.

## Implementation Plan

Three phases as above. Each phase is a landable PR (or small stack)
with its own test plan file under `docs/test-plans/`.

Milestones:

1. **P1 merged.** `mr-deed` adapter end-to-end for a single bash-only
   staff, streaming + resume + cancel work, concurrency test green.
   mr-deed is pinned at `v0.1.0`.
2. **P2 merged.** Native host tools gated by depth/role; a mr-deed
   staff can act as a dispatcher calling `delegate_task` and as an
   assignee calling `submit_result`. #256 orchestration path covered
   for the adapter.
3. **P3 merged.** Project configs under `.prj_assistant/mr-deed/`
   participate in config resolution; trust model documented.

mr-deed upgrades are explicit tag bumps in their own PR (§11). New
interface needs go to mr-deed as issues (§10).

Rollback: P1 is a pure additive change (new enum value + new code
paths); removing the enum rejects any staff that was created with
`adapter='mr-deed'` on load. P2 removes host-tool registration; mr-deed
staffs lose orchestration but keep bash. P3 removes a config-
resolution step; shipped and built-in configs still resolve.
