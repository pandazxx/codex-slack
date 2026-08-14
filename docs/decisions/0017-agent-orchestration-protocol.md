---
title: "ADR-0017: Agent orchestration and cross-agent communication protocol"
status: accepted
date: 2026-08-12
decision-makers: [architect, project-owner]
consulted: [engineer, tester]
informed: [doc-writer, users, sre]
---

## Context and Problem Statement

Today a topic hosts a single conversation between a user and one staff at a
time. Dispatch is one-shot: `POST /workspaces/{wid}/topics/{tid}/messages` →
`send_message` parses the `@mention` → `dispatch_to_staff` publishes a prompt
on `codex-slack/workspace/{wid}/topic/{tid}/prompt` → the agent replies on
`/response`. There is no way for one staff to hand a subtask to another, ask
a peer a clarifying question, or collaborate on a topic that logically wants
more than one specialist.

Users are asking for exactly that shape: a "lead" staff that decomposes a
piece of work and delegates the pieces, an "assignee" that does the work and
returns a structured result, and — critically — a way for the whole exchange
to remain a single durable audit trail in the topic rather than a fan-out of
side conversations. See GitHub issue
[#256](https://github.com/pandazxx/codex-slack/issues/256) for the full
discussion; this ADR records the decisions reached there.

The design must be additive: today's single-staff behaviour (user dispatches,
one staff replies to the user) must be a degenerate case of the new
mechanism, not a separate code path.

## Decision Drivers

- **Single audit log.** All cross-agent traffic must land in the existing
  `messages` table in the originating topic. No side channels, no per-agent
  correlation reconstruction, no broker-scoped ACLs. The topic is the ledger.
- **Session isolation is already correct.** Per-staff `staff_sessions` per
  topic already means a staff's LLM context contains only prompts dispatched
  to *it*. Cross-agent chatter in the same topic does not cross-contaminate
  contexts. We must not break this.
- **Reuse the dispatch path.** Every hop between agents (or between a user
  and an agent) must ride the existing `dispatch_to_staff` + MQTT machinery.
  Session sharing, `staff_sessions` bookkeeping, `sender="event"` loop
  prevention, and the streaming reply flow should fall out for free.
- **Master is the referee, LLMs are the workers.** Any decision that is
  fundamentally a judgment call (was the result acceptable? did the assignee
  understand the goal?) belongs to an LLM. Any decision that is a policy or
  budget (max delegation depth, max failure score, cycle detection) belongs
  to master-side code. Never blur the two.
- **Loop-safe and budget-bounded by construction.** A delegated task that
  bounces between staffs must terminate on a countable resource, not on good
  behaviour. The same principle already applies to event actions (loop
  prevention via `sender="event"`, ADR-0013 §5).
- **Explicit and observable.** Delegation must be an explicit act (a tool
  call, a row), not a free-text convention. Every hop must be a row an
  operator can point at in the DB.
- **Backward compatible.** Every existing topic must keep working with zero
  configuration changes. The v1 default (max_delegation_depth=1) permits the
  new mechanism to fire but requires an explicit tool call from the lead
  staff to do so.
- **Interop-friendly.** Where a standard exists for agent-to-agent task
  lifecycles (the Linux Foundation A2A protocol), borrow its semantics so
  our first-party model is compatible with a future adapter.

## Considered Options

### Transport shape

1. **Master-mediated, reply-to-sender via MCP tools** (chosen). All cross-agent
   traffic goes through master. Master exposes an MCP server to each agent
   container; agents call tools (`delegate_task`, `ask_sender`,
   `accept_result`, `reject_result`) that master turns into standard
   `dispatch_to_staff` calls. "Lead" is a contextual role: the dispatcher of
   a task is its sender.
2. **Direct agent-to-agent MQTT topics.** Each agent subscribes to a per-pair
   topic; addressing done via broker topics.
3. **Free-text delegation markers** parsed from agent output (e.g. a
   convention that any line starting with `[to @assignee]` is a delegation).

### Role model

A. **Reply-to-sender, no designated lead** (chosen). Any staff can be a
   dispatcher of a subtask. The dispatcher is whoever sent the last message
   in the task's causal chain. 0 leads (user → staff → user, today's
   behaviour) is a valid configuration expressible in the new envelope. N
   leads emerge naturally as tasks compose.
B. **Designated "lead" staff per topic**, configured as such, with a
   privileged tool surface.
C. **Team-topic mode** — a topic-level flag that toggles multi-agent
   behaviour.

### Task lifecycle

I. **A2A-inspired lifecycle** (chosen). Every `delegate_task` creates a task
   row with states `submitted → working → input-required → completed |
   failed | escalated`, borrowed from the Linux Foundation A2A protocol.
   Structured result envelope from the assignee.
II. **Free-form** — no lifecycle; assignees just reply, dispatchers just
    read.
III. **Full A2A wire-format compliance** — master implements the A2A HTTP +
     JSON-RPC + SSE surface for agent traffic.

### Failure judgment

α. **LLM judges, code counts** (chosen). The dispatcher decides whether an
   assignee's result meets its acceptance criteria via explicit
   `accept_result` / `reject_result` tool calls. Master maintains a
   numerical failure_score per task and enforces a configurable ceiling.
   Judgment applies at staff↔staff boundaries only; user↔staff traffic is
   never scored.
β. **Code judges via structured contracts.** Master parses each result
   against a schema and grades pass/fail.
γ. **No judgment** — the loop terminates only when the dispatcher stops
   delegating.

### Topology enforcement

T1. **Depth counter + role-scoped tool availability** (chosen). Each message
    carries a task depth (user-sent = 0; each `delegate_task` +1). The
    `delegate_task` tool is only exposed to agents whose current turn is at
    depth < `max_delegation_depth`. Assignees processing a delegated task
    get a restricted tool surface (`ask_sender` + result envelope only).
T2. **Free topology** — every staff can call every tool from every context.

### Concurrency model

C1. **Master serialises active delegations per topic** (chosen for v1). Same
    topic = same git worktree = same in-flight lock. Assignees see each
    other's commits sequentially.
C2. **Free concurrency with child branches** — each delegation forks a child
    branch/worktree; parallel fan-out.

### Escalation dispatcher feedback

E1. **Digest-on-close** (chosen). When an escalation opens, a direct user↔
    assignee channel is created scoped to that task. The dispatcher stays
    silent during escalation. When the escalation closes, master injects a
    digest of what happened into the dispatcher's next prompt.
E2. **Live cc** — every message in the escalation channel is also copied
    into the dispatcher's inbox.

## Decision Outcome

**Chosen:** **1 + A + I + α + T1 + C1 + E1.** Concretely:

1. **Master-mediated MCP transport.** Master runs an MCP server reachable
   from each agent container over the existing MQTT connection's control
   plane (or a dedicated stdio bridge established at agent-container
   startup — the design doc §4 covers the exact wiring). The tool surface
   is small and closed:

   | Tool | Available to | Purpose |
   |---|---|---|
   | `delegate_task(staff, goal, acceptance_criteria, context)` | Depth < `max_delegation_depth` | Hand a subtask to another staff |
   | `ask_sender(question)` | Any turn | Ask a clarifying question of whoever dispatched this turn |
   | `answer_question(task_id, answer)` | Dispatcher with a pending question on an own task | Answer an assignee's `ask_sender` as a structured, addressed message (free-text fallback if omitted) |
   | `submit_result(status, summary, artifacts)` | Assignee of a delegated task | Submit the structured result-of-record (implicit-result fallback if omitted) |
   | `accept_result(task_id)` | Dispatcher of `task_id` on its judgment turn | Close a task as completed |
   | `reject_result(task_id, feedback)` | Dispatcher of `task_id` on its judgment turn | Increment `failure_score` and re-dispatch |
   | `give_up_task(task_id, reason)` | Dispatcher of `task_id` | Explicitly escalate without further attempts |

   No direct agent-to-agent MQTT topics. No broker ACLs. No free-text
   delegation markers. Every hop is a tool call producing a `messages` row
   and (for `delegate_task`) a `tasks` row.

2. **Reply-to-sender role model.** "Lead" is contextual, not configured.
   The dispatcher of a task is whoever caused it (user or staff); the
   assignee replies to that dispatcher. Today's user↔staff exchange is
   expressible in the new envelope with zero configuration: user dispatches
   (sender_kind=`user`), staff replies to user (receiver_kind=`user`,
   `reply_to_message_id` set). No designated lead. No team-topic flag. 0–N
   leads emerge from task composition.

3. **Addressing envelope on `messages`.** Six new columns capture who talks
   to whom and which task the message belongs to:

   | Column | Type | Meaning |
   |---|---|---|
   | `sender_kind` | TEXT | `'user' | 'staff' | 'system'` |
   | `sender_name` | TEXT | Staff name for `staff`; null for `user`/`system` |
   | `receiver_kind` | TEXT | `'user' | 'staff' | 'system'` |
   | `receiver_name` | TEXT | Staff name for `staff`; null for `user`/`system` |
   | `task_id` | TEXT | FK-shaped reference into `tasks`; null for depth-0 messages |
   | `reply_to_message_id` | TEXT | The message this one answers; null for turn-openers |

   A single topic still holds all traffic. Per-staff context isolation
   already comes from `staff_sessions` (ADR-0009 §3). The UI renders
   sender→receiver badges on each bubble.

4. **`tasks` table with A2A-inspired lifecycle.** Each `delegate_task` call
   creates a row:

   ```
   tasks
     id                 TEXT PRIMARY KEY
     topic_id           TEXT NOT NULL     -- always the originating topic
     root_task_id       TEXT NOT NULL     -- self-reference for depth-0 tasks
     parent_task_id     TEXT              -- null for depth-0
     depth              INTEGER NOT NULL
     dispatcher_kind    TEXT NOT NULL     -- 'user' | 'staff'
     dispatcher_name    TEXT              -- staff name; null for user
     assignee_name      TEXT NOT NULL     -- staff name
     goal               TEXT NOT NULL
     acceptance_criteria TEXT
     state              TEXT NOT NULL     -- submitted | working | input-required
                                          -- | completed | failed | escalated
     failure_score      REAL NOT NULL DEFAULT 0.0
     created_at         TEXT NOT NULL
     updated_at         TEXT NOT NULL
     closed_at          TEXT
   ```

   State transitions are master-owned. LLMs cause them (via tool calls);
   master computes them. See the design doc for the exact table shape,
   indexes, and the state-machine diagram.

5. **Judgment/counting split.** The dispatcher judges quality (via
   `accept_result` / `reject_result`); master counts and enforces. Rejection
   adds `1.0` to `failure_score` and re-dispatches with the feedback text.
   An assignee's `ask_sender` clarifying question adds `0.5`. Both weights
   and the ceiling are configurable per-staff with a workspace-default
   cascade (same pattern as ADR-0009 §2). Scoring **only** applies at
   staff↔staff boundaries: a user asking a follow-up or rejecting a staff's
   answer never increments any counter.

   Judgment is mandatory: a dispatcher's judgment turn that ends without
   `accept_result` / `reject_result` / `give_up_task` has its reply routed
   to the assignee as a clarification scored `+question_weight`, so a
   judgment-avoiding ping-pong is bounded by `max_failure_score` and
   terminates in escalation — a dispatcher cannot silently stall a task
   (design doc §4.4).

   Configuration knobs (default → workspace → per-staff cascade):

   | Knob | v1 default | Effect |
   |---|---|---|
   | `max_failure_score` | `3.0` | Task escalates when exceeded |
   | `question_weight` | `0.5` | Increment per `ask_sender` in a delegated task |
   | `max_delegation_depth` | `1` | Ceiling on task depth (v1 default = user↔lead↔assignee) |
   | `max_tasks_per_root` | `20` | Fan-out fuse per root task |

6. **Depth-1 by default; deeper by configuration.** With
   `max_delegation_depth=1`, `delegate_task` is exposed to a staff whose
   current turn is at depth 0 (i.e. the user is the dispatcher). Assignees
   at depth 1 do not see `delegate_task` in their tool list at all. Deeper
   hierarchies are a config change, not a code change. No assignee↔assignee
   communication in v1 — any coordination goes through the shared
   dispatcher.

7. **Concurrency: same-topic serialisation.** Same topic = same worktree =
   same in-flight lock. Only one delegated task is in `working` state per
   topic at a time; siblings queue. This gives assignees a coherent view of
   the worktree (each sees the commits of the last assignee). Parallel
   fan-out via child branches is future work.

   Accepted caveat: if two dispatchers delegate to the same assignee, their
   turns interleave in one assignee session. Routing stays correct via
   `task_id`; if a session-per-task becomes necessary, the future fix is
   local to `_staff_session_key`.

8. **Human-in-the-loop and escalation.** Assignee questions go to the
   dispatcher via `ask_sender`; the task transitions to `input-required`
   and the parent chain pauses. No counters tick while `input-required`.
   If the dispatcher itself doesn't know the answer, it calls `ask_sender`
   in turn — questions bubble up one hop at a time.

   A question to the user is a normal message with `receiver_kind='user'`;
   the user answers with a message carrying `reply_to_message_id`, and
   master routes the answer to the asking staff (not the topic's default).
   Plain unaddressed user messages continue to route to the default/current
   staff as today.

   When `failure_score > max_failure_score` (or the dispatcher explicitly
   gives up), the task transitions to `escalated`: master prompts the
   dispatcher to post a summary to the user, then opens a direct
   user↔assignee channel scoped to that task only. The dispatcher stays
   silent during the escalation. When the user closes the escalation
   (resume / reassign / cancel), master injects a digest of what happened
   into the dispatcher's next prompt. This is a deliberate choice over live
   cc — see the alternatives section.

   An escalated task **retains** the per-topic in-flight lock: the lock
   protects the worktree, and an escalated task has by definition left it
   in an incomplete state — a sibling starting on that state (and the
   original later resuming against a diverged worktree) would be worse
   than the queueing delay. Sibling delegations stay queued (loudly, in
   the UI) until the user closes the escalation; resume/reassign continue
   straight to `working` on the untouched worktree, cancel releases the
   lock (design doc §8).

9. **Interop.** We borrow the A2A task lifecycle vocabulary now to leave the
   door open for a future adapter that exposes master as an A2A server for
   external agents. Full A2A wire-format compliance (HTTP/JSON-RPC/SSE) is
   explicitly out of scope for v1.

### Alignment with prior ADRs

- **ADR-0009 (Staff system).** Every delegation resolves the assignee
  through the existing `resolve_staff` cascade. Per-staff `staff_sessions`
  give context isolation for free.
- **ADR-0013 (Event-based staff actions).** The turn re-entry mechanism —
  "an assignee reply becomes a new prompt to the dispatcher" — is the same
  emit-event-then-dispatch shape as the event worker. The MCP tool call
  path produces `sender_kind='staff'` messages; the existing
  `topic_message_sent` gate (which fires only on `sender='user'`) already
  ignores them. No change to event-action loop prevention.
- **Streaming reply design.** Each delegated turn is an independent
  `dispatch_to_staff` call and streams through the existing `/chunk` path
  unchanged. No change to `chunks`.

### Implementation slicing

Split into three PR-sized phases (see design doc §11 for detail):

**(a)** Addressing envelope on `messages` + `tasks` table + master MCP
server with synchronous-ish `ask` and `delegate` v0. Depth-1 only. No
scoring, no escalation. Proves the plumbing end-to-end.

**(b)** Async delegation with turn re-entry: assignee reply becomes a
prompt to the dispatcher via the existing dispatch machinery. Task
lifecycle enforcement (state transitions, per-topic in-flight lock).

**(c)** Judgment loop (`accept_result` / `reject_result`), failure scoring,
escalation channel + digest-on-close.

### Consequences

- **Good**
  - Single audit trail: every hop is a row in `messages` with explicit
    sender→receiver in one topic. Debuggable by `SELECT * FROM messages
    WHERE topic_id = ? ORDER BY created_at`.
  - Backward compatible by construction: today's user↔staff exchanges
    become `sender_kind='user' → receiver_kind='staff'` and the new columns
    are the only diff. `max_delegation_depth=1` means the delegation
    machinery only fires when a staff actively opts in via tool call.
  - Reuses dispatch, session, streaming, and MQTT machinery — no parallel
    pipe to maintain.
  - Master-side counters are opaque to the LLM: budget enforcement can't
    be reasoned around by prompt injection or clever result formatting.
  - Topology enforced by (a) role-scoped tool availability and (b)
    master-side validation of every sender→receiver pair. Even a
    misbehaving agent that fabricates an MCP call is rejected at the
    server.
  - A2A-flavoured task lifecycle sets us up for future interop without
    committing to the full wire spec now.
- **Bad / accepted tradeoffs**
  - `messages` grows six columns. Additive migration (all nullable), but
    every reader needs to know about them.
  - Master-mediated means a chatty task (many hops) pays a full
    `dispatch_to_staff` per hop. Acceptable — each hop is already the same
    cost as one user message and is bounded by `max_tasks_per_root`.
  - Same-topic serialisation means a slow assignee blocks its siblings.
    Accepted trade for coherent worktree state; parallel fan-out is
    future work.
  - Two dispatchers delegating to the same assignee interleave in one
    session. Accepted with a known future fix (session-per-task).
  - Digest-on-close means the dispatcher doesn't see live escalation
    turns. Deliberate — avoids burning a dispatcher turn per message and
    prevents mid-intervention interjection.
  - Depth-1 default hides the delegation surface from most staffs. If an
    operator flips `max_delegation_depth` up without adding budget knobs,
    fan-out fuses (`max_tasks_per_root`, `max_failure_score`) are the only
    thing keeping runaway trees in check. Documented in the design doc.

### Confirmation

- Unit tests in `tests/master/test_orchestration.py`:
  - Addressing envelope round-trips through `dispatch_to_staff` and REST.
  - `delegate_task` is not exposed when current turn depth ≥
    `max_delegation_depth`.
  - `reject_result` increments `failure_score` by exactly `1.0`;
    `ask_sender` from an assignee increments by `question_weight` (default
    `0.5`); user↔staff turns never touch the counter.
  - Task state transitions match the state machine in the design doc; an
    LLM-driven transition that violates the machine is rejected at the
    MCP server with an explicit error.
  - `max_failure_score` breach transitions the task to `escalated` and
    opens a user↔assignee channel scoped to that task.
- Integration test:
  - User → lead `@architect` (depth 0) → `delegate_task` to `@engineer`
    (depth 1) → `@engineer` calls `ask_sender` → question is dispatched
    to `@architect` as a new prompt → `@architect` calls `ask_sender` to
    the user → user answers → answer routes back to `@architect` (not the
    default staff) via `reply_to_message_id` → `@architect` answers
    `@engineer` → `@engineer` completes → `@architect` calls
    `accept_result` → task closes; `messages.transcript` contains every
    hop.
- UAT (staging):
  - Backward compatibility: a topic with a single default staff and no
    orchestration configured behaves identically to today. `automated`.
  - Depth-2 configuration: with `max_delegation_depth=2`,
    `@architect` → `@engineer` → `@tester` completes and produces the
    correct three-layer audit trail. `needs-human` (visual inspection of
    the sender→receiver badges).

## Pros and Cons of the Options

### Transport shape

| Option | Pro | Con |
|---|---|---|
| 1 — Master-mediated MCP tools (chosen) | Single audit trail; reuses dispatch/session/MQTT machinery; explicit and observable per hop; central budget enforcement; loop-safe by construction | Every hop goes through master (extra RTT per hop — acceptable) |
| 2 — Direct agent↔agent MQTT | Lowest latency per hop | Loses single audit trail; needs broker ACLs; per-agent correlation reconstruction; no central budget or cycle guard; MCP-style tool discoverability requires reinventing |
| 3 — Free-text delegation markers | No new tool surface | Fragile parsing; unobservable; can't reject invalid delegations; contradicts "explicit and observable" driver |

### Role model

| Option | Pro | Con |
|---|---|---|
| A — Reply-to-sender, no designated lead (chosen) | Backward compatible (0 leads is a valid config); flexible topology (0–N leads); no configuration burden for the common case | Depth counter and sender resolution must be watertight — a bug in either compromises topology enforcement |
| B — Designated lead staff | Explicit role clarity | New configuration surface for every topic; forces a choice even for single-staff topics; hard to reconcile with dynamic lead handoff |
| C — Team-topic mode flag | Cheap to configure | Two behaviour modes to maintain; boundary cases at flag flip; contradicts "backward compatible by construction" |

### Task lifecycle

| Option | Pro | Con |
|---|---|---|
| I — A2A-inspired lifecycle (chosen) | Standard vocabulary; opens future interop path; states map directly to observable UI signals (spinner/input-required/completed) | We're bound to A2A's semantics for state names even if we later find better ones |
| II — Free-form | Minimal schema | No structured signal for "is this task done?"; escalation and judgment have no anchor row |
| III — Full A2A wire compliance | Immediate interop | Large surface (HTTP + JSON-RPC + SSE) to build and maintain for zero v1 external-agent demand |

### Failure judgment

| Option | Pro | Con |
|---|---|---|
| α — LLM judges, code counts (chosen) | Quality judgment stays in the model where it belongs; budget enforcement stays in code where it can't be argued with; clear split of responsibilities | Requires explicit tool calls (`accept_result`/`reject_result`) — LLMs must learn the convention |
| β — Code judges via schema | Machine-checkable | Can't judge quality of natural-language work; schema authoring is a whole feature by itself |
| γ — No judgment | Simplest | Loops terminate only on dispatcher exhaustion; no automatic escalation |

### Topology enforcement

| Option | Pro | Con |
|---|---|---|
| T1 — Depth counter + role-scoped tools (chosen) | Belt-and-braces: even a rogue MCP call is validated server-side; depth is a countable, testable invariant | Depth math must be exact across turn re-entry — off-by-one bugs are latent until deep-hierarchy configs |
| T2 — Free topology | Simplest possible | No cycle protection; no way to hide the delegation tool from assignees who shouldn't use it |

### Concurrency

| Option | Pro | Con |
|---|---|---|
| C1 — Same-topic serialisation (chosen for v1) | Coherent worktree state per assignee; no cross-assignee git contention; matches today's single-agent-per-topic invariant | Slow assignee blocks siblings; no parallel fan-out |
| C2 — Free concurrency + child branches | True parallel work | Requires per-task worktrees/branches; merge story; conflict resolution model — non-trivial design; deferred to a future ADR |

### Escalation dispatcher feedback

| Option | Pro | Con |
|---|---|---|
| E1 — Digest-on-close (chosen) | Dispatcher's turn budget isn't burnt per escalation message; user and assignee negotiate without a third party interjecting; single "here's what happened" turn resumes the parent flow cleanly | Dispatcher loses live visibility during escalation (mitigated: everything is still in the topic messages) |
| E2 — Live cc | Dispatcher stays informed in real time | Every escalation turn costs a dispatcher turn; dispatcher is tempted to interject mid-negotiation; harder to reason about turn semantics |
