---
title: "ADR-0018: Mr-deed agent adapter"
status: proposed
date: 2026-10-09
decision-makers: [architect, project-owner]
consulted: [engineer, tester]
informed: [doc-writer, users, sre]
---

## Context and Problem Statement

The platform supports Claude Code (ADR-0001, ADR-0005) and Codex (ADR-0014)
as agent backends. Both are CLI subprocesses that stream JSONL, keep their
own session state, and load MCP tools from a config file. We want to add
[`pandazxx/mr-deed`](https://github.com/pandazxx/mr-deed) — a fork of
mini-swe-agent v2 (Python package `mr_deed`, dist `mr-deed`) owned by the same
project-owner — as a third adapter so we can get fine-grained control of
agent flow and tool calls from inside our own process. Mr-deed's primary
role is a side-kick / orchestrator / assistant, with the ability to do
simple tasks end-to-end. See GitHub issue
[#265](https://github.com/pandazxx/codex-slack/issues/265) for the full
discussion (and [#256](https://github.com/pandazxx/codex-slack/issues/256)
for the orchestration protocol it will plug into).

Before this work, mr-deed differed from the existing adapters in four
load-bearing ways: it was in-process only (`DefaultAgent(model, env,
**config).run(task)`, no CLI contract), had no sessions (`run()` reset
`self.messages`), hardcoded `bash` as its only tool, and had no event
hooks or cancel path. It also loaded `.env` into `os.environ` at import
time.

Those gaps were closed upstream, following the boundary rule (point 12):
codex-slack filed them as mr-deed issues #6–#11, which landed in mr-deed
PR #13 and shipped as **mr-deed `v0.1.0`** (`3834d02`). The interface this
ADR builds on:

| Need | mr-deed `v0.1.0` API |
|---|---|
| Sessions | `DefaultAgent.from_trajectory(path, model, env, **overrides)` restores a `save()` file; `continue_run(task)` appends a user message rendered from `AgentConfig.continue_template` (default `{{ task }}`). Step/cost/time limits reset per run or continuation; `total_cost` / `total_n_calls` span the conversation. |
| Live observation | `DefaultAgent(..., on_event=callable)`, called synchronously with `(kind, payload)`. Kinds: `system`, `user`, `assistant`, `action` (before it runs), `observation`, `format_error`, `exit`. Payloads are the dicts appended to `agent.messages`. Exactly one `exit` per run. Handlers must not raise. |
| Cancel | `DefaultAgent(..., cancel_event=threading.Event())`; `agent.cancel()` sets it and calls `env.interrupt()`. `LocalEnvironment.interrupt()` kills the running process group, or stays pending and kills the next command as soon as it spawns. The agent calls `reset_interrupt()` at the start of every run. The run exits with `exit_status="Cancelled"`. |
| Host tools | `mr_deed.Tool(name, description, parameters, handler)`; `DefaultAgent(..., tools=[...])` (replaceable per run). Tool-call models send `[bash, *tools]`. Actions are `{"name", "arguments", "tool_call_id"}` (bash also keeps `command`). A handler gets `arguments` and returns `{"output", ...}`; an exception becomes an observation with `returncode -1`. Text-based models reject tools. |
| Embedding hygiene | `import mr_deed` has no side effects on `os.environ`, stdout or disk (dotenv and banner live in `mr_deed.run`). Per-agent limits use only the agent's own counters. |
| Pinning | `v<version>` tags are created automatically on version bumps. |

## Decision Drivers

- **Deep integration.** Project-owner wants fine-grained control of agent
  flow and tool calls (future: agent memory management). This rules out
  the CLI-subprocess shape used by `claude-code` and `codex`.
- **Reuse existing machinery.** `staff_sessions`, the `agent-llm`
  ThreadPoolExecutor in `mqtt_loop.py`, the `/chunk` streaming pipeline,
  and the master-side orchestration protocol (ADR-0017) are reused as-is;
  master changes are limited to the adapter enum and the `agent_config`
  staff field (point 3).
- **Per-turn env hygiene.** `DISPATCH_TOKEN`, `TASK_DEPTH`, `TOPIC_ID`,
  `AGENT_NAME`, `PROMPT_MESSAGE_ID`, `WORKSPACE_ID`, `MASTER_URL` change
  every turn and must never leak into `os.environ` where another
  concurrent turn could read the wrong value.
- **Clean cross-repo boundary.** codex-slack and mr-deed are co-owned but
  separately versioned. Interface requirements go in as mr-deed issues
  and codex-slack consumes them through a pinned release tag.
- **Orchestration-first role.** Phasing must get mr-deed to the point
  where it can call `delegate_task` / `ask_sender` / `submit_result`
  (ADR-0017) early — that is the main intent — before expanding to
  project configs.
- **Generic agent interface.** `hermes` is an experiment inside mr-deed.
  The adapter must not special-case it; any supervisor behaviour is
  configuration.
- **Lessons.** Env values that cross process/thread boundaries need a
  test per hop (lessons-learned 2026-08-16). Runtime dependency pins need
  an upper bound on the major version (lessons-learned 2026-08-14) — we
  pin by git tag (`v0.1.0`).

## Considered Options

### Integration shape

1. **Import mr-deed as a library and run `agent.run()` on the existing
   `agent-llm` thread pool** (chosen). Per-turn env goes into
   `LocalEnvironment(env=...)`, never `os.environ`.
2. **Import mr-deed as a library but spawn a child process per turn.**
   JSONL on stdout, same shape as `_stream_codex_once`.
3. **Wrap a mr-deed CLI** mirroring the Codex adapter.

### Host-tool integration

A. **Native in-process Python tools registered alongside bash** (chosen).
   The tool list for a turn is `[bash] + <codex-slack tools selected by
   TASK_DEPTH and role>` per ADR-0017 T1. Handlers call master's HTTP
   endpoints directly, reusing the client code from the orchestrate/notes
   MCP servers; no MCP hop.
B. **Bash CLI shim** (`orch delegate_task ...`, `notes list_workspace_notes
   ...`). Zero changes to mr-deed; works for any bash-only agent.
C. **MCP client inside mr-deed.** Mr-deed speaks MCP to the master
   endpoint over a local transport.

### Sessions

I. **Persist trajectory per `session_id` on a volume and resume on
   `is_new_session=false`** (chosen). Linear history, no compaction in v1.
II. **No session support — every turn is a fresh run.**
III. **In-memory session map keyed by `staff_sessions`**, lost on restart.

### Credentials

α. **Reuse the existing config-var → container env mechanism**; litellm
   reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / etc. (chosen).
β. **Add a dedicated mr-deed credential blob** stored like
   `CODEX_AUTH_JSON`.

## Decision Outcome

**Chosen:** **1 + A + I + α.** Concretely:

1. **New adapter value `mr-deed`.** Added to `_VALID_ADAPTERS` in
   `src/master/staffs.py`. Agent side gets `elif adapter == "mr-deed":
   _run_mr_deed(...)` in `src/agent/mqtt_loop.py::_process_prompt`
   alongside `_run_claude` and `_run_codex`. `staff_sessions` is
   unchanged. The staff record and dispatch payload gain one field,
   `agent_config` (point 3). UI changes: an `agent_config` YAML editor in
   the staff form, and new branches in the chunk-event classifier
   (`TopicChat.vue`) for the `mr_deed.*` event types (point 6).

2. **In-process runner on the `agent-llm` ThreadPoolExecutor.** No child
   process. The three usual in-process risks are each handled explicitly:
   - **Per-turn env:** `LocalEnvironment` already accepts a per-instance
     `env` dict; `DISPATCH_TOKEN`, `TASK_DEPTH`, `TOPIC_ID`, `AGENT_NAME`,
     `PROMPT_MESSAGE_ID`, `WORKSPACE_ID`, `MASTER_URL` go there, never
     into `os.environ`.
   - **Crash isolation:** the runner wraps `agent.run()` in a
     catch-everything that becomes an error reply (same contract as the
     Codex adapter's exit-status mapping).
   - **Cancel:** the runner keeps the running agent per
     `reply_message_id`; the cancel handler calls `agent.cancel()`, which
     stops the run at its next query or action and kills the in-flight (or
     about-to-start) bash command.
   - **Per-turn objects:** each turn builds its own `LocalEnvironment`,
     model and agent (`from_trajectory` on resume). Nothing mr-deed-side
     is shared between concurrent turns.

   **Where it runs:** everything mr-deed executes runs in the
   per-workspace **agent container**, in the `mqtt_loop` process: the
   imported library, the litellm model calls (agent container →
   provider), the `bash` tool (a `LocalEnvironment` subprocess with
   `cwd=<topic worktree>`, the same place claude/codex run their
   commands today), and the host-tool handlers. Master only resolves the
   staff (including `agent_config`), dispatches the turn, and serves the
   orchestration/notes HTTP endpoints that the handlers call. The
   package is installed in the image master and agents share by default,
   but only the agent process imports `mr_deed`.

3. **Config resolution — first match wins:**
   0. **codex-slack config:** the staff's own `agent_config` (mr-deed
      YAML stored on the staff record and edited in the web GUI). It is
      resolved by the existing staff cascade (topic → workspace → global,
      full override, ADR-0009 §2), so a global staff carries a global
      config and a workspace/topic staff overrides it. Master sends the
      resolved YAML in the dispatch payload.
   1. `<worktree>/.prj_assistant/mr-deed/<name>.yaml` (project-specific;
      arrives in P3).
   2. `/opt/codex-slack/config/mr-deed/<name>.yaml` (shipped configs).
   3. mr-deed built-ins.

   Tier 0 is resolved on master. Tiers 1–3 are resolved on the agent,
   because only the agent container has the worktree and imports
   `mr_deed`. The staff `agent` field names the config for tiers 1–3. `staff.model` overrides
   `model.model_name`; `staff.system_prompt` is appended to
   `system_template`. The runner **always forces** `environment.type=local`
   with `cwd=<worktree>` — a repo config cannot switch execution to
   docker or any other environment.

4. **Tool set per turn** = `bash` (always; `LocalEnvironment` with the
   per-turn env dict and the existing
   `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` submission convention) +
   codex-slack in-process Python tools: orchestration (`delegate_task`,
   `ask_sender`, `answer_question`, `submit_result`, `accept_result`,
   `reject_result`, `give_up_task` — the last two once ADR-0017 phase (c)
   ships them) gated per turn by `TASK_DEPTH`, plus notes tools. Gating
   is exactly the rule set `src/orchestrate_mcp/server.py` applies today
   (`delegate_task` iff depth < max; `submit_result` iff depth ≥ 1;
   `answer_question`/`accept_result` iff depth = 0; `ask_sender` always),
   extracted into one shared function so both adapters' surfaces cannot
   drift. Handlers call master's HTTP endpoints directly — **no MCP hop**.
   The client code in `src/orchestrate_mcp/` and `src/notes_mcp/` is
   refactored from import-time `os.environ` capture into call-time
   functions that take a per-turn context; the MCP servers become thin
   wrappers over the same functions. Master still validates every call.
   Host tools are tool-call-mode only; text-based mini configs cannot use
   them.

5. **Sessions.** Persist the trajectory per `session_id` on a volume at
   `/workspace/sessions/mr-deed/<session_id>.traj.json`. On
   `is_new_session=false` the runner loads prior messages, renders the
   new prompt through a `continue_template` (config key; defaults to the
   raw prompt), appends it as a user message,
   and continues the loop. `staff_sessions` and `session_scope` semantics
   are preserved. Linear history, no compaction in v1 — documented
   limitation.

6. **Streaming.** Each agent event (assistant, action, observation, exit)
   is emitted as a `/chunk` frame through the existing pipeline, as
   `mr_deed.*`-typed events that the frontend classifier maps to its
   existing display kinds; the
   submission becomes `last_response`. `LimitsExceeded`, `TimeExceeded`,
   `RepeatedFormatError`, `Cancelled` map to error replies that name the
   exit status. Transcript = `messages`.

7. **Credentials.** Existing config-var → container env path; litellm
   reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / etc. **Codex OAuth
   `auth.json` and Claude subscription login are not usable** — a
   mr-deed staff needs a real API key. Documented.

8. **Limits (cost/step/wall) left to the mr-deed agent config** —
   mr-deed exposes those knobs today and we don't duplicate them in the
   staff/workspace cascade.

9. **Trust.** Repo-committed `.prj_assistant/mr-deed/*.yaml` is trusted
   like `CLAUDE.md` for now (we force `environment.type=local`
   regardless). Revisit later.

10. **Packaging.** Installed in the agent base image as
    `mr-deed @ git+https://github.com/pandazxx/mr-deed@v0.1.0`. Upgrades
    are deliberate tag bumps in their own PR. Lesson from the `mcp` 2.0
    break: unbounded version ranges on runtime deps cause unrelated PRs
    to fail CI; we pin by git tag instead.

11. **hermes is just an experiment.** Target a generic agent interface;
    no hermes special-casing.

12. **Boundary rule.** codex-slack and mr-deed keep a clear boundary.
    Interface requirements are filed as mr-deed issues and consumed via a
    pinned tag; codex-slack does not subclass or patch mr-deed internals.
    Milestone 1 (all shipped in `v0.1.0`):

    | # | Requirement | mr-deed issue |
    |---|---|---|
    | R1 | Session continuation (resume with new prompt) | [mr-deed#6](https://github.com/pandazxx/mr-deed/issues/6) |
    | R2 | Event hook / callback API | [mr-deed#7](https://github.com/pandazxx/mr-deed/issues/7) |
    | R3 | Cooperative cancellation incl. in-flight bash | [mr-deed#8](https://github.com/pandazxx/mr-deed/issues/8) |
    | R4 | Pluggable host tools alongside bash | [mr-deed#9](https://github.com/pandazxx/mr-deed/issues/9) |
    | R5 | Embedding hygiene (no import-time dotenv; per-agent limits) | [mr-deed#10](https://github.com/pandazxx/mr-deed/issues/10) |
    | R6 | Versioned tags for pinning | [mr-deed#11](https://github.com/pandazxx/mr-deed/issues/11) |

13. **No codex-slack-side stopgaps.** The earlier plan to start P1 with
    wrappers (session subclass, `add_messages` override, cancel/env
    subclass, dotenv guard) is dropped: every one of them is covered by
    `v0.1.0`.

14. **Memory management is out of scope.** Session resume (R1) is the
    foundation; agent-memory lands in a later ADR.

### Phasing

Order explicitly swapped by project-owner: codex-slack tools (#256
support) land **before** project configs.

| Phase | codex-slack scope | mr-deed deps |
|---|---|---|
| **P1** | `mr-deed` adapter, in-process runner on `agent-llm` pool, config resolution for staff `agent_config` (GUI-editable) + shipped + built-in, streaming, session resume, cancel | R1, R2, R3, R5, R6 — shipped in `v0.1.0` |
| **P2** | codex-slack tools on top of bash → #256 orchestration support | R4 — shipped in `v0.1.0` |
| **P3** | project configs under `.prj_assistant/mr-deed/` | none |

### Consequences

- **Good**
  - In-process execution gives us the deep integration intent: host tools
    are plain Python handlers, we can observe every message, and future
    agent-memory work has a hook surface to build on.
  - Few master changes: the adapter enum and the `agent_config` staff
    field/payload key. Streaming, session and orchestration machinery is
    reused as-is.
  - `tool set = bash + codex-slack tools` matches ADR-0017 T1 depth/role
    gating without inventing a parallel enforcement path. Master still
    validates every call server-side.
  - Clean cross-repo boundary. Everything codex-slack needs is public
    mr-deed API at a pinned tag; there are no codex-slack-side
    subclasses of mr-deed internals to maintain.
- **Bad / accepted tradeoffs**
  - Litellm is a sizeable transitive dependency; it noticeably grows the
    agent base image. Measured in P1; documented in the design doc.
  - API-key billing only (no Codex OAuth / Claude subscription login).
  - Linear history / no compaction in v1 — long sessions will eventually
    hit context and cost limits; compaction is deferred.
  - Text-based mini configs cannot use host tools (tool-call-mode only).
    Documented limitation.
  - Shared boundary with mr-deed means some integration bugs will need
    coordinated fixes across two repos.

### Confirmation

- Unit tests in `tests/agent/test_mr_deed_adapter.py`:
  - Config resolution order (worktree → shipped → built-in).
  - Per-turn env delivery into `LocalEnvironment` and into bash's env —
    **one test per env-transport hop** per the 2026-08-16 lesson.
  - Streaming event mapping (assistant / action / observation / exit →
    chunk kinds; submission → `last_response`).
  - Session resume loads prior messages and appends the new prompt
    through `continue_template`.
  - Cancel: `agent.cancel()` from another thread → run exits
    `Cancelled` → the in-flight bash command is killed.
  - Exit-status mapping: `LimitsExceeded`, `TimeExceeded`,
    `RepeatedFormatError`, `Cancelled` → named error replies.
- Concurrency test: two `_run_mr_deed` calls on the pool with different
  per-turn env dicts do not cross-contaminate.
- Integration test (P2 onwards): a `mr-deed` staff calls `delegate_task`
  via the in-process tool; master records the task row and dispatches
  the assignee exactly as it would for a Claude Code dispatcher.
- UAT: a mr-deed staff finishes a bash-only task end-to-end with
  streaming visible in the UI; a resumed session continues after
  restart.

## Pros and Cons of the Options

### Integration shape

| Option | Pro | Con |
|---|---|---|
| 1 — Import as library, run on agent-llm pool (chosen) | Deep integration (in-process tools, observable flow, future memory hooks); reuses existing threadpool and streaming; per-turn env isolated via `LocalEnvironment(env=...)` | Depended on mr-deed cancel (R3), event hooks (R2) and embedding hygiene (R5) — all shipped in `v0.1.0` |
| 2 — Library + child process per turn | Cancel is "kill PID"; `os.environ` isolated by process | Loses in-process tool integration (would need IPC); extra fork/exec cost per turn; defeats the deep-integration driver |
| 3 — CLI wrapper | Mirrors Codex adapter | Mr-deed's `mini` CLI is single-shot and exposes no tool or flow hooks; forces subprocess serialisation and defeats the deep-integration intent |

### Host-tool integration

| Option | Pro | Con |
|---|---|---|
| A — Native in-process Python tools (chosen) | No MCP hop; depth/role gating lives next to the dispatcher; handler reuses existing HTTP client; works with existing streaming | Needs R4 (pluggable host tools) in mr-deed; text-based mini configs excluded |
| B — Bash CLI shim | Zero mr-deed changes; works for any bash-only agent | Separate `orch` / `notes` CLI binary to ship and version; one more env-transport hop to test; defeats the deep-integration intent |
| C — MCP client in mr-deed | Reuses MCP server as-is | Adds a transport layer we specifically avoid for other adapters; another process to manage; brittle on the dependency pin that broke us before |

### Sessions

| Option | Pro | Con |
|---|---|---|
| I — Persist trajectory per `session_id` (chosen) | Preserves `staff_sessions` + `session_scope` semantics; survives restart; uses mr-deed's own `save()` / `from_trajectory` / `continue_run` | Linear history; long sessions hit context/cost limits without compaction (deferred) |
| II — No session support | Simplest | Breaks `staff_sessions` contract; every turn is cold |
| III — In-memory session map | Avoids a volume | Lost on restart; invisible in audits; same context-growth problem |

### Credentials

| Option | Pro | Con |
|---|---|---|
| α — Reuse existing config-var → env (chosen) | Zero new UX; litellm already reads these | API keys only — Codex OAuth / Claude subscription login not supported |
| β — Dedicated credential blob | Matches the Codex adapter's `CODEX_AUTH_JSON` shape | New sensitive-var slot; litellm does not read it natively |
