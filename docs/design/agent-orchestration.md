# Design: Agent orchestration and cross-agent communication

**Status:** accepted
**Author:** architect
**Date:** 2026-08-12
**Related ADRs:** [ADR-0017](../decisions/0017-agent-orchestration-protocol.md);
builds on ADR-0009 (Staff system), ADR-0013 (Event-based staff actions), and
the streaming reply design.
**Issue:** [#256](https://github.com/pandazxx/codex-slack/issues/256)

## Context

A topic today hosts one conversation between a user and one staff at a time.
Every message is either `sender='user'` or `sender='agent'` (with
`sender='event'` reserved for event-triggered dispatches, per ADR-0013). The
`messages` table already has enough structure to record the *what* of every
exchange but nothing about the *who-to-whom*: it assumes the receiver of a
user message is implied (the staff resolved from the `@mention` or the
topic default) and the receiver of an agent reply is always "the user".

We want the same topic to support multi-staff collaboration:

- A user asks `@architect` for a design.
- `@architect` decomposes the work and delegates parts to `@engineer` and
  `@tester`.
- `@engineer` needs to know a preference and asks a clarifying question.
- The question routes back to `@architect`; if `@architect` doesn't know
  either, it asks the user.
- The user's answer routes back to `@architect`, who answers `@engineer`.
- `@engineer` completes; `@architect` accepts the result and moves on.

Every hop must land in the topic as an auditable message. Per-staff LLM
context isolation (via `staff_sessions`, ADR-0009 §3) must continue to hold:
`@engineer`'s context contains only prompts dispatched to `@engineer`, not
the full topic transcript. Today's single-staff topics must keep working
with zero configuration change.

The design converged in GitHub issue #256 across the 10-12 August 2026
discussion. This document formalises the settled decisions and fills in
schema, protocol, and mechanism detail.

## Goals

- One durable audit trail per topic: every hop between user/staff pairs is a
  row in `messages` with explicit `sender_kind` / `sender_name` /
  `receiver_kind` / `receiver_name` / `task_id` / `reply_to_message_id`.
- Delegation is an explicit MCP tool call, not a free-text convention.
- "Lead" is a contextual role — the dispatcher of a task is its sender. 0–N
  leads supported without configuration; today's user↔staff mode is a
  degenerate zero-lead case.
- Task lifecycle borrowed from the Linux Foundation A2A protocol
  vocabulary (`submitted / working / input-required / completed / failed /
  escalated`); wire format is not A2A-compliant in v1 but the vocabulary
  keeps that path open.
- Master mediates every hop; no direct agent↔agent MQTT.
- Judgment (accept/reject a result) is an LLM concern; counting and
  enforcement (failure_score, depth, fan-out fuse) are master's concern.
- Loop-safe and budget-bounded by construction, not by good behaviour.
- Reuses `dispatch_to_staff` + MQTT + `staff_sessions` end-to-end. Streaming
  chunks work unchanged for every hop.
- Backward compatible: existing topics with existing staffs keep working
  with zero configuration.

## Non-Goals

- **Full A2A wire-format compliance** (HTTP/JSON-RPC/SSE). We borrow the
  task lifecycle vocabulary; interop with external A2A agents is a future
  adapter, not this design.
- **Parallel fan-out via per-task child branches.** Same topic = same
  worktree. Concurrent assignees serialise. Parallel branches are future
  work.
- **Assignee↔assignee direct communication.** Any coordination between
  peers goes through their shared dispatcher, one hop at a time. No peer
  channel exists in v1.
- **Session-per-task isolation.** If two dispatchers delegate to the same
  assignee, their turns interleave in one assignee session. Routing stays
  correct via `task_id`. Session-per-task is a known future fix.
- **Live cc of escalation traffic to the dispatcher.** Escalation is a
  direct user↔assignee channel scoped to the task; dispatcher gets a
  digest on close, not a running feed.
- **Deep-hierarchy defaults.** `max_delegation_depth` defaults to 1
  (user↔lead↔assignee). Deeper trees require an explicit config change.
- **Cross-topic delegation.** A task lives in one topic. Cross-topic
  coordination is out of scope.

## Design

### 1. Communication matrix

The full set of allowed `(sender_kind, sender_name) → (receiver_kind,
receiver_name)` pairs. Master validates every dispatch against this table.

| Sender | Receiver | Allowed | Notes |
|---|---|---|---|
| user | staff | ✅ | Today's dispatch. `task_id` = null (depth 0) or set (answer to `ask_sender`). |
| staff (dispatcher) | staff (assignee) | ✅ | `delegate_task` opens a new task; the assignee reply and any follow-ups reuse the task_id. |
| staff (assignee) | staff (dispatcher) | ✅ | Reply to the delegation, or `ask_sender` clarification. Never fires the `staff→staff` scoring path if the sender is the *user* (see §5). |
| staff (assignee) | user | ✅ **only during escalation** | Direct escalation channel, scoped to the escalated task_id. `task.state='escalated'`. |
| staff | staff (self) | ❌ | Self-delegation is rejected at the MCP server. |
| staff | staff (unrelated to any active task) | ❌ | Cold outreach not permitted; must go through a task. |
| assignee | assignee (peer under same dispatcher) | ❌ | Peers coordinate via the shared dispatcher. |
| system | any | ✅ | Master-generated system messages (escalation-open notices, digests). |

Master rejects any dispatch whose sender→receiver pair is not in the ✅
rows. Rejection surfaces as an MCP tool error to the calling agent; the
agent's turn continues (the tool call failed but did not abort the turn).

### 2. Data model

Two changes:

**(a)** Add six columns to `messages` — all nullable, additive migration.

**(b)** Add a new `tasks` table.

#### 2.1 `messages` — additive columns

Current schema (`src/master/db.py:77-89`):

```sql
CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    topic_id         TEXT NOT NULL REFERENCES topics(id),
    sender           TEXT NOT NULL,          -- 'user' | 'agent' | 'event'
    agent_name       TEXT,
    text             TEXT NOT NULL,
    transcript       TEXT,
    usage_json       TEXT,
    attachments_json TEXT,
    event_action_id  TEXT,
    silent           INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);
```

Added via `_MIGRATIONS` (SQLite `ALTER TABLE ... ADD COLUMN`, guarded with
the existing try/except pattern for idempotency):

```sql
ALTER TABLE messages ADD COLUMN sender_kind         TEXT;   -- 'user' | 'staff' | 'system'
ALTER TABLE messages ADD COLUMN sender_name         TEXT;   -- staff name; null for user/system
ALTER TABLE messages ADD COLUMN receiver_kind       TEXT;   -- 'user' | 'staff' | 'system'
ALTER TABLE messages ADD COLUMN receiver_name       TEXT;   -- staff name; null for user/system
ALTER TABLE messages ADD COLUMN task_id             TEXT;   -- FK-shaped ref into tasks
ALTER TABLE messages ADD COLUMN reply_to_message_id TEXT;   -- FK-shaped ref into messages
CREATE INDEX IF NOT EXISTS idx_messages_task
    ON messages (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_reply_to
    ON messages (reply_to_message_id);
```

Relationship to the existing `sender` column:

| existing `sender` | new `sender_kind` | Meaning |
|---|---|---|
| `user` | `user` | Human input (unchanged) |
| `agent` | `staff` | Staff reply (rename of concept — same underlying rows) |
| `event` | `staff` (with `event_action_id` set) | Event-triggered dispatch (unchanged) |
| — (new) | `system` | Master-generated notice (escalation-open, digest) |

`sender` is kept for backward compatibility with existing code paths. New
readers should prefer `sender_kind`. A backfill migration populates
`sender_kind`/`receiver_kind` for existing rows: `sender='user'` →
`(sender_kind='user', receiver_kind='staff', receiver_name=<default staff
at row time>)`; `sender='agent'` → `(sender_kind='staff',
receiver_kind='user')`. Backfill is best-effort — historical rows without a
resolvable default staff get `receiver_kind='staff', receiver_name=NULL`
and the UI renders them with a "(unknown)" badge.

No FKs on `task_id` / `reply_to_message_id`: SQLite ALTER TABLE cannot add
FKs after the fact, and application-side validation catches invalid
references at dispatch time.

#### 2.2 `tasks` table

New table, appended to `_SCHEMA` in `src/master/db.py`:

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    topic_id            TEXT NOT NULL REFERENCES topics(id),
    root_task_id        TEXT NOT NULL,          -- self-reference for depth-0 tasks
    parent_task_id      TEXT,                   -- null iff depth = 0
    depth               INTEGER NOT NULL,
    dispatcher_kind     TEXT NOT NULL CHECK (dispatcher_kind IN ('user', 'staff')),
    dispatcher_name     TEXT,                   -- staff name; null iff dispatcher_kind='user'
    assignee_name       TEXT NOT NULL,          -- staff name
    goal                TEXT NOT NULL,
    acceptance_criteria TEXT,
    state               TEXT NOT NULL CHECK (state IN (
                            'submitted',
                            'working',
                            'input-required',
                            'completed',
                            'failed',
                            'escalated'
                        )),
    failure_score       REAL NOT NULL DEFAULT 0.0,
    result_summary      TEXT,                   -- set on completed | failed
    result_artifacts    TEXT,                   -- JSON: [{kind:'commit'|'file', ref:...}, ...]
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT                    -- set on completed | failed | escalated
);
CREATE INDEX IF NOT EXISTS idx_tasks_topic         ON tasks (topic_id);
CREATE INDEX IF NOT EXISTS idx_tasks_root          ON tasks (root_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_topic_state   ON tasks (topic_id, state);
CREATE INDEX IF NOT EXISTS idx_tasks_parent        ON tasks (parent_task_id);
```

Notes:

- `root_task_id` is a self-reference for depth-0 tasks. This makes the
  `max_tasks_per_root` fan-out fuse a trivial `COUNT(*) WHERE root_task_id
  = ?` and lets the UI collapse a whole delegation tree.
- **`depth = 0` case.** A depth-0 task is the user dispatching directly to
  a staff. In the default configuration (`max_delegation_depth=1`) this is
  the only kind of task that a user creates. We create depth-0 task rows
  lazily on first delegation — a pure user↔staff round-trip that never
  calls `delegate_task` does not create a `tasks` row (that would be
  needless churn for the common case).
- `state` values borrowed verbatim from the A2A task lifecycle.
- `failure_score` is REAL to allow `question_weight` half-increments.
- `result_summary` / `result_artifacts` populated when the assignee calls
  the `submit_result` MCP tool (or by the implicit-result fallback — see
  §4.3).
- No FK on `dispatcher_name` / `assignee_name` to `staffs.name` because
  staff names are not unique across scopes (ADR-0009 §2 override
  semantics); resolution goes through `resolve_staff` at every hop.

### 3. Task state machine

```mermaid
stateDiagram-v2
    state "input-required" as input_required
    [*] --> submitted: delegate_task
    submitted --> working: first prompt dispatched (lock acquired)
    working --> input_required: assignee ask_sender
    input_required --> working: dispatcher answers
    working --> working: reject_result (score ≤ max) — re-dispatch with feedback
    working --> completed: accept_result
    working --> escalated: reject_result breaches max_failure_score
    working --> escalated: give_up_task
    escalated --> working: user resumes or reassigns (lock retained, score reset)
    escalated --> failed: user cancels (lock released)
    completed --> [*]
    failed --> [*]
```

Transitions and who causes them:

| From → To | Trigger | Actor |
|---|---|---|
| — → `submitted` | `delegate_task` MCP call | Dispatcher LLM |
| `submitted` → `working` | Assignee's first prompt is dispatched (per-topic lock acquired) | Master |
| `working` → `input-required` | Assignee calls `ask_sender` | Assignee LLM |
| `input-required` → `working` | Dispatcher calls `answer_question` (or the free-text fallback, §4.1) | Dispatcher LLM |
| `working` → `completed` | Dispatcher calls `accept_result` on assignee's result | Dispatcher LLM |
| `working` → `working` (with feedback) | Dispatcher calls `reject_result` | Dispatcher LLM; master increments `failure_score += 1.0` and re-dispatches |
| `working` → `escalated` | `failure_score > max_failure_score` after an increment, or dispatcher explicitly calls `give_up_task` | Master (score) or dispatcher LLM (give_up) |
| `escalated` → `working` | User selects "resume" (same assignee) or "reassign" (new assignee). `failure_score` reset | Master |
| `escalated` → `failed` | User selects "cancel"; the per-topic lock releases | Master |
| any active → `failed` | Unrecoverable master-side error (e.g. assignee staff deleted mid-flight) | Master |

Note that resume/reassign goes straight back to `working`, not through
`submitted`: an escalated task **keeps** the per-topic in-flight lock
(§8) — the worktree stays protected for the whole escalation, so there is
no queue to re-enter and no sibling has touched the workspace in the
meantime.

Illegal transitions attempted by an LLM tool call (e.g.
`accept_result(task_id)` on a task in `input-required`) return an MCP
error; state does not change. No LLM can directly set `state` — every
transition is master-computed from tool calls or events.

### 4. Master MCP server

Master exposes an MCP server per agent container. The server is spun up as
part of the same container handoff that today wires the MQTT connection
(details in the implementation plan §11). Every tool call carries the
calling staff's identity and the current `topic_id` in the MCP session
context — the agent does not need to (and cannot) pass these as arguments.

#### 4.1 Tool signatures

```python
def delegate_task(
    staff: str,                     # target staff name
    goal: str,                      # natural-language description of the subtask
    acceptance_criteria: str,       # what "done" looks like for the dispatcher
    context: str | None = None,     # optional briefing text, prepended to first prompt
) -> DelegateResult:
    """Hand a subtask to another staff. Creates a task row; returns task_id."""
    # Available only while current_depth < max_delegation_depth.
    # Fails with tool error if:
    #   - staff cannot be resolved via resolve_staff()
    #   - staff == self (self-delegation)
    #   - fan-out fuse hit (root task has >= max_tasks_per_root children)
    #   - per-topic in-flight lock currently held by another task
    #     (returns "queued" — assignee dispatch waits)

class DelegateResult(TypedDict):
    task_id: str
    state: Literal['submitted', 'queued']
    queued_position: int | None    # non-null iff state == 'queued'


def ask_sender(question: str) -> AskResult:
    """Ask a clarifying question of whoever dispatched this turn.

    Sends `question` as a new message with receiver = the dispatcher of the
    current task (or the user if depth == 0). Transitions the task to
    'input-required' if this call is inside a delegated task.
    """
    # Increments failure_score by question_weight (default 0.5) if the
    # current turn is at depth >= 1 (staff→staff scoring boundary).
    # Never increments for depth-0 turns (user↔staff).

class AskResult(TypedDict):
    message_id: str
    task_state: str      # 'input-required' if inside a delegated task, else 'n/a'


def answer_question(task_id: str, answer: str) -> AnswerResult:
    """Answer a pending ask_sender question on a task you dispatched.

    Records the answer as a structured, addressed message (receiver = the
    asking assignee) and returns the task to 'working'. Master dispatches
    the answer to the assignee when the caller's turn ends.
    """
    # Fallback: if a question turn ends with free text and no
    # answer_question call, master uses the pending-dispatch context to
    # treat the turn's final text as the answer. Structured call is
    # primary; the fallback only keeps a non-calling agent from stalling
    # the task.

class AnswerResult(TypedDict):
    task_id: str
    state: Literal['working']


def submit_result(
    status: Literal['completed', 'failed'],   # assignee's own assessment
    summary: str,                             # one-paragraph result summary
    artifacts: list[Artifact] | None = None,  # commits/files produced
) -> SubmitResult:
    """Submit the structured result of the current delegated task.

    Available only on depth-≥1 turns (inside a delegated task). Records
    result_summary / result_artifacts on the task row as the
    result-of-record and marks the result as pending judgment; when the
    assignee's turn ends, master dispatches the judgment turn to the
    dispatcher. Task state stays 'working' until the dispatcher judges.
    Calling it twice in one turn overwrites (last call wins).
    """
    # Fallback: if a delegated turn ends without any submit_result call,
    # master treats the turn's final reply text as an implicit result
    # (status='completed', summary=<reply text>, artifacts=[]) so a
    # forgetful assignee cannot stall the task. The system prompt template
    # instructs assignees to always call submit_result.

class Artifact(TypedDict):
    kind: Literal['commit', 'file']
    ref: str                                  # sha or path

class SubmitResult(TypedDict):
    task_id: str
    state: Literal['working']


def accept_result(task_id: str) -> AcceptResult:
    """Close a task as completed.

    Callable only by the task's dispatcher on the turn immediately after
    the assignee's result was submitted. The result-of-record is whatever
    the assignee last passed to `submit_result` (or the implicit-result
    fallback).
    """

class AcceptResult(TypedDict):
    task_id: str
    state: Literal['completed']


def reject_result(task_id: str, feedback: str) -> RejectResult:
    """Reject the assignee's result and re-dispatch with feedback.

    Increments failure_score by 1.0. If failure_score > max_failure_score
    after the increment, state transitions to 'escalated' and the tool
    returns state='escalated' — the dispatcher's next turn opens with the
    escalation prompt (not a re-dispatch).
    """

class RejectResult(TypedDict):
    task_id: str
    state: Literal['working', 'escalated']
    failure_score: float


def give_up_task(task_id: str, reason: str) -> GiveUpResult:
    """Explicitly escalate a task without further attempts."""

class GiveUpResult(TypedDict):
    task_id: str
    state: Literal['escalated']
```

#### 4.2 Tool availability by role

Master computes the tool surface per-agent-per-turn. The MCP server
publishes only the tools the current turn is allowed to call.

| Current turn context | `delegate_task` | `ask_sender` | `answer_question` | `submit_result` | `accept_result` / `reject_result` | `give_up_task` |
|---|---|---|---|---|---|---|
| Depth-0 turn (user is dispatcher), depth < `max_delegation_depth` | ✅ | ✅ (asks user) | ✅ when a question on an own task is pending | ❌ (nothing to submit to) | ✅ (over own delegated child) | ✅ |
| Depth-0 turn, depth == `max_delegation_depth` | ❌ | ✅ | ✅ (same condition) | ❌ | ✅ | ✅ |
| Depth-≥1 turn (staff is assignee) | ❌ in v1 (see below) | ✅ (asks dispatcher) | ❌ (questions only flow assignee→dispatcher) | ✅ | ❌ | ❌ |
| Escalation channel turn (assignee↔user, task in `escalated`) | ❌ | ✅ (asks user) | ❌ | ✅ (resubmit after direct fixes) | ❌ | ❌ |

**Invariant: every staff-addressed message is born from an MCP tool call**
(`delegate_task`, `ask_sender`, `answer_question`, `submit_result`,
`reject_result` feedback). An agent's free-text turn output is
user-addressed by default. This is how master always knows the receiver
without parsing prose — the structured information travels in the tool
call, and the pending-dispatch context (§7) is only consulted for the
documented implicit fallbacks (missing `submit_result` / missing
`answer_question` / missing judgment, §4.3–§4.4), so a non-calling agent
degrades gracefully instead of stalling.

In v1, `delegate_task` is exposed to depth-0 turns only (since
`max_delegation_depth=1`). When the operator raises
`max_delegation_depth`, the tool becomes available to deeper turns
automatically — the code path is `if current_depth < max_delegation_depth:
expose delegate_task`. Assignees at the depth ceiling never see the tool
in their list.

#### 4.3 Structured result envelope

An assignee returns its result by calling the **`submit_result` MCP tool**
(§4.1) — the same explicit-and-observable principle as every other hop.
The tool validates the envelope (`status`, `summary`, `artifacts`) at call
time, writes it to the task row as the result-of-record, and master
dispatches the judgment turn to the dispatcher when the assignee's turn
ends. No free-text tail-block parsing on the happy path.

Fallback for robustness: if a delegated turn ends without a
`submit_result` call, master synthesises an implicit result from the
turn's final reply text (`status='completed'`, `summary=<reply text>`,
`artifacts=[]`). A forgetful assignee therefore degrades to a weaker
envelope instead of stalling the task. The assignee system-prompt template
always instructs calling `submit_result`.

#### 4.4 Judgment is mandatory — no silent third option

The dispatcher's judgment turn must end in exactly one of `accept_result`,
`reject_result`, or `give_up_task`. There is deliberately **no unscored
"reply with more text" escape**: if the judgment turn ends with plain text
and no judgment tool call, master routes that text to the assignee as a
clarification message on the task **and scores it `+question_weight`**
(symmetric with assignee-side `ask_sender`); the assignee's next
`submit_result` re-opens judgment.

This closes the loophole where a dispatcher that neither accepts nor
rejects would leave the task orphaned: the reply always has a receiver
(the assignee, via the task's addressing), and every non-judgment
round-trip moves `failure_score`, so an accept/reject-avoiding ping-pong
is bounded by `max_failure_score` and terminates in escalation — where
the user takes over. A dispatcher literally cannot stall a task
indefinitely; the worst case is a few scored clarification loops followed
by escalation.

### 5. Failure scoring

Scoring applies exclusively at staff↔staff boundaries.

| Event | Score change | Scope |
|---|---|---|
| Dispatcher calls `reject_result` | `+1.0` | The rejected `task_id` |
| Assignee calls `ask_sender` inside a delegated task | `+question_weight` (default `0.5`) | The current `task_id` |
| Dispatcher ends a judgment turn with plain text instead of a judgment tool (routed to assignee as clarification, §4.4) | `+question_weight` (default `0.5`) | The current `task_id` |
| User rejects a staff answer or asks a follow-up | `0.0` | Never scored |
| Staff `ask_sender` in a depth-0 turn (asking the user) | `0.0` | Never scored |
| Assignee reply in `input-required` (dispatcher answers) | `0.0` | Score is untouched during `input-required` |

When a `reject_result` call causes `failure_score > max_failure_score`, the
task transitions to `escalated` in the same commit. Escalation opens a
direct user↔assignee channel (see §6).

Configuration (staff-cascade, matching ADR-0009):

```
max_failure_score       default 3.0  workspace → per-staff override
question_weight         default 0.5  workspace → per-staff override
max_delegation_depth    default 1    workspace → per-staff override
max_tasks_per_root      default 20   workspace → per-staff override
```

Resolution: per-staff (on the *dispatcher*, since it owns the delegation)
first, then workspace, then hard-coded default. When the dispatcher is the
user (depth 0), the workspace default applies.

### 6. Human-in-the-loop and escalation

#### 6.1 Question routing

An `ask_sender` call from staff S sends a message to whoever is S's
sender on the current task turn:

- Depth-0 turn where user is dispatcher → receiver = user.
- Depth-≥1 turn where staff D is dispatcher → receiver = D.

The message row carries `sender_kind='staff'`, `sender_name=S`,
`receiver_kind='user' or 'staff'`, `receiver_name=D.name or NULL`,
`task_id=<current>`, `reply_to_message_id=<the dispatcher's last message
that started this turn>`. If receiver = staff, master dispatches the
question as a new prompt to D via `dispatch_to_staff` (the standard turn
re-entry mechanism — see §7).

If receiver = user, the message lands in the topic. The UI shows a badge:
"@S is asking you a question". The user's answer must carry
`reply_to_message_id` pointing at S's question; the REST endpoint parses
that and dispatches the answer to S (not the topic default). A plain
unaddressed reply from the user continues to route to the default/current
staff, unchanged from today.

Questions can bubble: if the depth-1 assignee asks its dispatcher, and the
dispatcher itself doesn't know, the dispatcher issues its own `ask_sender`
to the user. Each hop is one turn. Failure scoring pauses on all tasks in
the ancestry chain while any of them is `input-required`.

#### 6.2 Escalation

Trigger: `failure_score > max_failure_score` (after a `reject_result`
increment), or the dispatcher explicitly calls `give_up_task(task_id,
reason)`.

Sequence:

1. Master sets `task.state = 'escalated'` and writes `closed_at = NULL`
   (escalation is active, not closed).
2. Master dispatches a *system-message prompt* to the dispatcher: "Task
   `<task_id>` (goal: `<goal>`) has been escalated. Please post a brief
   summary of what happened for the user, then this task is out of your
   hands until it closes." The dispatcher's reply is a normal
   `sender_kind='staff', receiver_kind='user'` message tagged with the
   task_id.
3. Master opens the direct channel: subsequent messages between the
   assignee and the user carrying the escalated `task_id` route directly.
   The `staff→user` pair is normally reserved for escalation only (see the
   communication matrix, §1) and master's dispatch validator permits it
   for this task_id specifically.
4. The dispatcher receives no further hops on this task while it is
   escalated. `sender_kind='staff' (dispatcher) → receiver_kind='staff'
   (assignee)` for this task_id is rejected while state is `escalated`.
5. User closes with one of three actions surfaced in the UI:
   - **Resume** — dispatcher takes it back. State → `working`;
     `failure_score` reset to `0.0`. The task kept the per-topic lock
     throughout the escalation (§8), so it continues immediately on an
     untouched worktree.
   - **Reassign** — user picks a new assignee. Master rewrites
     `assignee_name`, resets `failure_score` to `0.0`, state → `working`;
     the new assignee inherits the worktree exactly as the escalation
     left it.
   - **Cancel** — state → `failed`, `closed_at` set; the per-topic lock
     releases and the next queued delegation dispatches (worktree left
     as-is — see §8).
6. On close, master synthesises a digest and injects it into the
   dispatcher's next prompt (turn re-entry): "While task `<task_id>` was
   escalated: `<N>` messages exchanged; user chose `<action>`;
   final assignee reply summary: `<...>`". The digest is one turn's worth
   of context — never a running feed.

`failure_score` reset semantics are: on Resume and Reassign, reset to
`0.0` (fresh start). On Cancel, the task is closed so it doesn't matter.
On close-without-user-action (timeout — currently not supported, see open
questions), the same reset rule would apply if we add it later.

### 7. Dispatch and turn re-entry

The core insight: every hop — user prompt, delegation, clarifying question,
answer, escalation notice, digest injection — is a standard call to
`dispatch_to_staff` (`src/master/dispatch.py:69`). The addressing envelope
determines who the recipient is; nothing about `dispatch_to_staff`'s inner
mechanics changes.

```mermaid
sequenceDiagram
    participant U as User
    participant API as POST /messages
    participant M as Master
    participant MCP as MCP server
    participant D as dispatch_to_staff
    participant MQTT
    participant A1 as Agent (Architect)
    participant A2 as Agent (Engineer)

    U->>API: text="Design the auth flow" (no @mention)
    API->>D: dispatch, sender=user, receiver=@architect, task_id=null
    D->>MQTT: publish /prompt
    MQTT->>A1: deliver prompt (depth=0)

    A1->>MCP: delegate_task(staff="engineer", goal=..., criteria=...)
    MCP->>M: validate depth, resolve staff, acquire topic lock
    M->>M: INSERT tasks row (state=submitted, depth=1)
    M-->>A1: task_id=T, state=submitted
    A1->>MQTT: publish /response ("delegated to engineer")
    MQTT->>M: response
    M-->>U: WS broadcast (architect's delegation note, visible to user)

    M->>D: dispatch first prompt of T, sender=@architect, receiver=@engineer
    Note over M: task T state → working
    D->>MQTT: publish /prompt (depth=1)
    MQTT->>A2: deliver prompt

    A2->>MCP: ask_sender("which token format?")
    MCP->>M: turn context is depth=1, task T
    Note over M: score += question_weight (0.5), state → input-required
    A2->>MQTT: publish /response (turn ends)
    MQTT->>M: response
    M->>D: dispatch question, sender=@engineer, receiver=@architect
    D->>MQTT: publish /prompt
    MQTT->>A1: deliver prompt (question turn for task T)

    A1->>MCP: answer_question(T, "use JWT")
    MCP->>M: record answer for task T
    Note over M: state → working
    A1->>MQTT: publish /response (turn ends)
    MQTT->>M: response
    M->>D: dispatch answer, sender=@architect, receiver=@engineer
    D->>MQTT: publish /prompt
    MQTT->>A2: deliver prompt (answer for task T)

    A2->>MCP: submit_result(status=completed, summary=..., artifacts=[commit sha])
    MCP->>M: record result-of-record on task T
    A2->>MQTT: publish /response (turn ends)
    MQTT->>M: response
    M->>D: dispatch judgment turn, sender=@engineer, receiver=@architect
    D->>MQTT: publish /prompt
    MQTT->>A1: deliver prompt (judgment turn for task T)

    A1->>MCP: accept_result(T)
    MCP->>M: task T → completed, release topic lock
    M-->>A1: state=completed
    A1->>MQTT: publish /response (final answer for the user)
    MQTT->>M: response
    M-->>U: WS broadcast (final reply, receiver=user)
```

The generalisation of ADR-0013's event-dispatcher pattern is exactly this:
an agent's `/response` on the wire *may* be inspected by master to see if
it's the final reply of the causal chain or a hop that should re-enter the
dispatch machinery.

Concrete master-side logic on receiving `/response` (extends
`_save_agent_response` in `src/master/mqtt_client.py`):

1. **Check what the turn's MCP tool calls already staged.** The primary
   routing signal is explicit: `ask_sender`, `answer_question`,
   `submit_result`, and `reject_result` each recorded a structured,
   addressed outbound message during the turn (§4.2 invariant). When the
   `/response` arrives (turn end), master dispatches those staged
   messages — receiver, task_id, and reply chain all came from the tool
   call, nothing is inferred.
2. Insert the message row for the turn's free text (as today), with
   `sender_kind='staff'`. If a staged tool message exists, the free text
   is user-visible commentary (e.g. the delegation note in the diagram);
   if none exists, apply the turn-context fallback below.
3. Fallback only — look up the *pending dispatch context*: master keeps a
   small in-memory map `pending_dispatches[message_id] → {task_id,
   dispatcher_kind, dispatcher_name, turn_kind}` populated when
   `dispatch_to_staff` publishes a prompt. If a turn that was expected to
   call a tool (result turn, question turn, judgment turn) ended with
   free text only, this context determines the implicit interpretation
   (§4.3–§4.4: implicit result / implicit answer / scored clarification)
   and therefore the receiver. The map is rebuilt from `messages` +
   `tasks` on process restart by a startup scan of open tasks.
4. Compute the next hop:
   - If the receiver resolved to `user`, no re-entry — the message is
     user-visible and the flow ends.
   - If the receiver resolved to a staff, call `dispatch_to_staff` with
     sender = the responding staff, receiver as resolved, task_id
     preserved, `reply_to_message_id` = the message from step 1/2.
5. Task state transitions are computed alongside step 4 per §3.

Step 3 is the "turn re-entry" mechanism. It's exactly the shape of
ADR-0013's event-dispatcher: an event (here: assignee `/response`) causes
a new `dispatch_to_staff` call. The `sender='event'` gate on
`topic_message_sent` (ADR-0013 §5) already excludes these re-entry
dispatches from firing user-facing message hooks — with the new envelope,
we adjust the gate to `sender_kind='user'` (which excludes both `'staff'`
and `'system'`).

### 8. Concurrency: per-topic in-flight lock

Master maintains a per-topic in-memory lock (`asyncio.Lock` keyed by
`topic_id`). `delegate_task` acquires it before dispatching the assignee's
first prompt. The lock releases only when the task **closes** —
`completed` or `failed` — not on every hop within the task, and **not on
escalation**.

**An escalated task keeps the lock.** The lock's real job is protecting
the worktree, not scheduling fairness: an escalated task has, by
definition, left work in an incomplete state. Releasing the lock would let
a sibling delegation start on top of that half-done worktree, and the mess
compounds when the escalated task later resumes against a worktree that
has since diverged. So while a task is `escalated`:

- Sibling delegations stay queued. The escalation modal makes the
  blockage visible: "Task T-42 is escalated and holding this topic's
  queue — N delegations waiting."
- The direct user↔assignee channel can safely touch the worktree — the
  lock guarantees nobody else is.
- **Resume**/**Reassign** transition the task straight back to `working`
  (no re-queue — it never gave the lock up), with the worktree exactly as
  the escalation left it.
- **Cancel** closes the task (`failed`) and releases the lock. The
  worktree is left as-is; the next queued delegation sees whatever the
  cancelled task left behind — same sequential-coherence model as a
  completed task's leftovers. If the abandoned state is unusable, the
  user's cancel flow can instruct the next dispatch (or a direct message)
  to clean up first; automatic stash/revert-on-cancel is future work.

The cost is that a human-latency escalation blocks sibling delegations
for its whole duration. That is the intended trade: correctness of the
shared worktree over topic throughput. Escalations are loud in the UI
precisely so they get resolved promptly.

If a second `delegate_task` on the same topic arrives while the lock is
held, the MCP call returns `{state: 'queued', queued_position: N}`; the
caller LLM can wait, cancel, or do other work. Master queues the pending
delegations in a per-topic FIFO. When the current task closes, the next
queued delegation acquires the lock and dispatches.

This guarantees:

- At most one delegated task in `working` state per topic at any time.
- Assignees see a coherent worktree (previous assignee's commits are
  already in the branch by the time the next assignee's turn starts).

Trade-offs:

- A slow assignee blocks every sibling delegation on the topic.
- The dispatcher itself is *not* blocked — it can still process
  `ask_sender` returns from the current assignee, and can queue additional
  delegations.
- Depth-0 (direct user↔staff) messages are unaffected by the lock — the
  lock only serialises `delegate_task`-driven dispatches. A user can still
  type `@architect` while `@engineer` is working on a delegated task.

Parallel fan-out with per-task child branches is future work (see
Non-Goals).

#### Same-assignee interleaving

If two dispatchers on the same topic delegate to `@engineer`, both tasks
land in one `@engineer` LLM session (per `staff_sessions`). Routing stays
correct: every prompt carries `task_id` in the system-prompt preamble
(added by master during dispatch), and `@engineer`'s replies are tagged
with the task_id in the pending-dispatch map. `@engineer`'s LLM context
may end up mixing content from two tasks — accepted for v1 (known caveat
in ADR-0017 Consequences). The future fix is session-per-task, local to
`_staff_session_key`.

### 9. Loop and budget guards

| Guard | Where | Behaviour |
|---|---|---|
| `max_delegation_depth` | MCP `delegate_task` — refuses to expose the tool | Tool absent from turn's tool list; agent physically cannot delegate |
| `max_tasks_per_root` | MCP `delegate_task` — computes `COUNT(*) FROM tasks WHERE root_task_id = ?` | Returns tool error `fan_out_exceeded` |
| `max_failure_score` | On `reject_result` increment | Task transitions to `escalated`; tool returns `state='escalated'` |
| Cycle detection | On `delegate_task`, check ancestor chain via `parent_task_id` | If proposed assignee is already an ancestor of the current task, reject with `cycle_detected` |
| Self-delegation | On `delegate_task`, sender == receiver | Rejected with `self_delegation` |
| Sender→receiver validation | On every `dispatch_to_staff` call | Rejected against the communication matrix (§1); returns MCP tool error |
| Judgment stall | Dispatcher's judgment turn ends without accept/reject/give_up (§4.4) | Reply routed to assignee as clarification and scored `+question_weight` — ping-pong is bounded by `max_failure_score` → `escalated` |

Every guard writes an audit line: `orchestration.guard_hit
guard=<name> task_id=<...> topic_id=<...>`.

### 10. UI

The topic chat UI renders each bubble with a sender→receiver header:

```
┌────────────────────────────────────────────────────────────┐
│ @architect → @engineer  · Task T-42 · 2 min ago            │
│                                                            │
│ Please implement the JWT verification middleware described │
│ in the design doc §4.2. Use the existing `AuthContext`     │
│ pattern from `src/auth/context.py`.                        │
│                                                            │
│ Acceptance: unit tests pass; `just test` green.            │
└────────────────────────────────────────────────────────────┘
```

Sender and receiver badges are always shown. `task_id` is a link that
expands the whole task's message chain (filtered view over the topic).

The topic sidebar gains a "Tasks" panel listing active/recent tasks with
their state; clicking a task filters the topic view to that task's
messages.

Escalated tasks surface a modal at the top of the chat: "Task T-42 has
been escalated. Choose an action: Resume · Reassign · Cancel." Direct
assignee↔user messages during escalation carry a red-border badge to make
the direct channel visually distinct.

Detailed styling and Vue component layout are out of scope for this
document; frontend implementation follows the pattern used for the
streaming reply bubble (`docs/design/streaming-agent-reply.md`).

### 11. Implementation slicing

Three PR-sized phases, each independently landable.

#### Phase (a) — Envelope + MCP server + synchronous delegation

- Migration: six new columns on `messages`, `tasks` table, indexes.
- `dispatch_to_staff`: accept `sender_kind`, `sender_name`, `receiver_kind`,
  `receiver_name`, `task_id`, `reply_to_message_id` kwargs; default to
  today's user↔staff behaviour when not provided.
- Master-side sender→receiver validator (rejects invalid pairs).
- MCP server per agent container. v0 tool surface: `delegate_task` and
  `ask_sender` with synchronous-ish semantics (the calling agent blocks on
  the MCP response, which arrives once master has written the task row and
  dispatched the assignee prompt — but not waiting for the assignee to
  finish).
- `max_delegation_depth=1` hard-coded initially; config knob in phase (b).
- Backfill migration for existing `messages`.
- UI: sender→receiver badges. No task panel yet.
- Tests: envelope round-trip; MCP tool availability by depth; validator
  reject cases; the depth-1 happy path (user → architect → engineer →
  architect → user).

#### Phase (b) — Async delegation + turn re-entry + task lifecycle

- Turn re-entry: master's `/response` handler dispatches the reply back to
  the dispatcher (staff or user) via `dispatch_to_staff`.
- Pending-dispatch map + startup scan for restart recovery.
- Task state machine (`submitted → working → input-required → completed`)
  implemented and enforced. Illegal LLM-driven transitions return MCP
  errors.
- `submit_result` and `answer_question` MCP tools + their implicit
  fallbacks (§4.1, §4.3).
- `accept_result` MCP tool.
- Per-topic in-flight lock; `delegate_task` queueing.
- Configuration knobs (`max_delegation_depth`, `max_tasks_per_root`)
  wired through the staff cascade.
- UI: tasks panel; task-filtered message view.
- Tests: full async delegation chain; state-machine coverage; in-flight
  lock behaviour; multi-dispatcher-same-assignee interleaving.

#### Phase (c) — Judgment loop and escalation

- `reject_result` and `give_up_task` MCP tools.
- Mandatory-judgment gate (§4.4): non-judgment dispatcher replies route
  to the assignee as scored clarifications.
- Failure scoring (dispatcher rejection = +1.0, assignee `ask_sender` or
  dispatcher non-judgment clarification = +question_weight).
- Escalation-close semantics: lock retained through escalation;
  Resume/Reassign → `working` with score reset; Cancel releases the lock.
- Escalation transition and direct user↔assignee channel (validator
  permits `staff→user` pair only when task is in `escalated`).
- Escalation-close digest injection.
- UI: escalation modal with Resume / Reassign / Cancel; red-border direct
  channel styling.
- Config: `max_failure_score`, `question_weight`.
- Tests: rejection increments; escalation transition on threshold;
  reassign/resume/cancel round-trips; digest injection.

## Alternatives Considered

### Direct agent↔agent MQTT topics

Each agent subscribes to per-pair topics (e.g. `codex-slack/pair/{a}/{b}`).
Delegation writes directly to the peer's inbox topic.

Rejected. Loses the single audit trail (traffic is split across per-pair
topics; reconstructing the causal chain requires cross-topic correlation).
Requires broker ACLs to keep agents from reading each other's traffic.
There is no central place for loop, cycle, or budget enforcement — every
agent would have to enforce independently, and a misbehaving agent
bypasses the whole thing. The master-mediated design keeps master as the
single referee.

### Full A2A protocol adoption in v1

Master implements the A2A HTTP + JSON-RPC + SSE surface end-to-end; agents
speak A2A instead of MCP.

Deferred, not rejected on principle. The A2A wire format is large and the
v1 driver is first-party agents, not external interop. We adopt A2A's task
lifecycle vocabulary now (so future adapter work is a wire-format layer,
not a rethink) and leave the transport-level compliance for a future ADR.
Master-as-A2A-server is a plausible future direction; nothing in v1
precludes it.

### Designated lead / team-topic mode

A topic has a configured "lead" staff with a privileged tool surface,
distinct from ordinary staffs. Or a topic-level `team_mode` flag toggles
multi-agent behaviour.

Superseded by the reply-to-sender model (issue #256 comment, 2026-08-12).
Reply-to-sender achieves the same outcome without a configuration burden
on every topic and without two parallel behaviour modes. Today's
single-staff topics become the zero-lead degenerate case for free.

### Separate child topic per delegation

Each `delegate_task` creates a child topic (with its own worktree, its own
`staff_sessions`, its own audit log). The parent topic references the
child.

Rejected for v1. Context isolation is already provided by per-staff
`staff_sessions` — a child topic would provide isolation we don't need
while fragmenting the audit trail. Discovery of related work would require
UI traversal of parent/child topic graphs. Same-worktree serialisation
already gives assignees a coherent view of each other's commits.

Child topics remain a valid option for future large isolated subtasks (a
sub-project that legitimately wants its own conversational scope); we
leave that door open.

### Live cc of escalation traffic to the dispatcher

Every message in the escalation channel is also delivered to the
dispatcher as a normal turn.

Rejected. Costs a full dispatcher turn per escalation message (with the
same per-turn latency and LLM cost as any other prompt). Invites the
dispatcher to interject mid-negotiation, which defeats the purpose of the
direct channel. Digest-on-close is a single-turn summary that catches the
dispatcher up cleanly without turn-cost blow-up.

### Output-parsing / free-text delegation markers

Instead of MCP tool calls, agents emit conventional markers in their
reply text (e.g. `[to @engineer]: ...`) that master parses to trigger
delegation.

Rejected. Fragile — depends on the LLM following the convention
exactly and on master's parser being tolerant of every variation. Not
observable — a failed parse silently drops the delegation. Not
auditable — the intent lives in prose, not a tool-call record. Explicit
MCP tools keep the audit log authoritative and let master reject invalid
calls with a machine-readable error the agent can react to.

## Open Questions

- [ ] **Digest format details.** What exactly goes into the
      escalation-close digest injected into the dispatcher's next prompt?
      Current thinking: 3–5 short lines (turn count, user's chosen action,
      final assignee reply summary, key artifacts). Needs a concrete
      template plus a length cap. Owner: architect + doc-writer.
- [ ] **`failure_score` reset semantics on `resume`.** We reset to `0.0` on
      Resume and Reassign (§6.2). Is that correct? Arguments for keeping a
      partial memory (e.g. reset to `max_failure_score / 2`) so
      persistently-failing tasks can't loop forever with a cooperative
      user. Arguments for full reset (current choice): user has explicitly
      re-entered the flow with new context; treat it as a fresh start.
      Ship the current choice; revisit if we see abuse in practice.
- [ ] **UI rendering of deep hierarchies.** With
      `max_delegation_depth > 1`, the sender→receiver badge plus task_id
      link may not be enough — operators will want a tree view. Do we
      build a tree-shaped task panel in phase (b), or wait for demand?
      Owner: frontend engineer.
- [ ] **Escalation timeout.** Today an escalated task waits indefinitely
      for the user to choose Resume/Reassign/Cancel. Should there be a
      configurable timeout after which master auto-cancels? Not urgent —
      escalated tasks are visible in the tasks panel and the user can
      close them at any time. Revisit if pile-up becomes a problem.
- [ ] **Pending-dispatch map durability.** The in-memory
      `pending_dispatches[message_id] → task_context` map is rebuilt from
      SQL on restart. For very long-running tasks with many hops, the
      startup scan is O(N) over unresolved staff messages in unclosed
      tasks. Do we need an explicit `pending_dispatches` table to make
      this O(1) per lookup on cold start? Not urgent — N is small at
      current scale.
- [ ] **`give_up_task` UX signal.** When the dispatcher gives up on a
      task, we currently jump straight to escalation. Should the UI first
      ask the user "the dispatcher gave up — do you want to resume, reassign,
      or cancel?" (i.e. essentially the escalation flow with a different
      opening line). Probably yes; scope for phase (c).
- [ ] **Cross-container MCP transport specifics.** ADR decides "master
      exposes an MCP server to agent containers"; the exact transport
      (stdio bridge over the agent's existing MQTT connection vs. a
      dedicated per-container Unix socket vs. HTTP-over-loopback) is an
      implementation-time call in phase (a). Prototype both; land whichever
      is simpler to wire from the existing `src/agent/mqtt_loop.py`
      startup path.
- [ ] **Backfill of `sender_kind` for historical `sender='event'` rows.**
      Historical event-triggered rows map to `sender_kind='staff'` with
      `event_action_id` set. Confirm that no existing consumer of
      `sender='event'` breaks when we introduce the new column; the
      migration keeps `sender` untouched, so this should be a pure add.

## Implementation Plan

Three phases as above. Each phase is a landable PR (or a small stack) with
its own test plan file under `docs/test-plans/`.

Milestones:

1. Phase (a) merged: backward-compatible envelope, MCP tool surface for
   `delegate_task` + `ask_sender`, depth-1 happy path proven end-to-end.
   No behaviour change for existing topics; new orchestration behaviour
   opt-in via MCP tool calls only.
2. Phase (b) merged: async delegation with turn re-entry, task lifecycle
   enforcement, in-flight lock. UI tasks panel. Sufficient for real
   multi-agent use with cooperative agents.
3. Phase (c) merged: judgment loop, failure scoring, escalation.
   Sufficient for real multi-agent use with fallible agents. Feature is
   considered production-ready.

Rollback: phase (a) is a pure add (nullable columns + new table + new
tool endpoints + backward-compatible dispatch_to_staff signature); safe
to revert without touching data. Phase (b) adds turn re-entry — revert
requires disabling the re-entry hook in the `/response` handler; task
rows written by the reverted code are harmless (no reader breaks).
Phase (c) adds scoring and escalation — revert by disabling
`reject_result` and the escalation-state transitions; existing tasks in
`escalated` state need manual cleanup (documented in the lessons-learned
knowledge base at revert time).
