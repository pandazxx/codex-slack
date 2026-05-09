# v3.0 System Architecture

*Status:* accepted (implemented through v3 slices 1–12)
*ADR:* [0005 v3.0 System Architecture](../decisions/0005-v3-system-architecture.md)

## Context

v3.0 is a major operating-model change. The prior system routes user messages
from Slack or Discord to a single agent container per channel. v3.0 replaces
the chat platform dependency with a self-hosted web frontend and restructures
the operational model around workspaces and topics.

## Goals

- Self-hosted chat frontend; no Slack or Discord dependency
- Workspace (repo) → Topic (thread) hierarchy with per-topic git worktrees
- Real-time bi-directional agent communication (thinking spinner, response streaming)
- Persistent LLM sessions per topic for both Claude Code and Codex
- Structured durable storage (SQLite) for workspaces, topics, and chat history
- Preserved: master as control plane, Podman container lifecycle, agent image
  model, CD daemon

## Non-Goals

- Migration from v2 data (agents.json, thread state) — fresh deployment only
- Mobile push notifications
- Multi-master or high-availability storage (deferred to PostgreSQL migration)
- Slack/Discord adapter compatibility

## Design

### Runtime Roles

```
CI/CD container
  - manages master container lifecycle (unchanged from v2)

Master container
  - FastAPI HTTP server
      - serves compiled Vue 3 frontend (static files)
      - REST API for workspace/topic/agent management
  - WebSocket hub
      - browser clients connect here for real-time updates
  - MQTT client
      - publishes prompts to agent containers
      - subscribes to agent response and status messages
      - bridges agent events to connected WebSocket clients
  - SQLite database (workspace/topic/chat/session registry)
  - Topic manager (worktree lifecycle)
  - Agent container lifecycle via Podman (unchanged from v2)

Mosquitto container  [NEW]
  - MQTT broker
  - message bus between master and agent containers
  - separate container in compose stack

Agent container  (one per workspace)
  - Container name: codex-agent-{workspace_id}
  - Named volume: codex-claude-{workspace_id} → /home/appuser/.claude
  - Named volume: codex-codex-{workspace_id} → /home/appuser/.codex
  - MQTT client
      - subscribes to prompt messages for its workspace
      - publishes response and status messages
  - LLM session manager
      - claude-code adapter: claude --print --verbose --output-format stream-json --dangerously-skip-permissions [--resume <id>]
      - codex adapter: codex exec --json --dangerously-bypass-approvals-and-sandbox -s danger-full-access --ephemeral -o <tempfile> [-m <model>] <prompt>
  - Mounts workspace volume (worktrees per topic)
```

### Operating Model

A *workspace* maps to one GitHub repository and one agent container. It holds a
set of registered staff agent configurations (e.g. `engineer`, `tester`, each
backed by Claude Code or Codex).

A *topic* is a chat thread within a workspace. Each topic has:
- a feature branch
- a git worktree at `/workspace/worktrees/<topic-id>/`
- one LLM session per staff agent (created on first mention, resumed on
  subsequent turns)

The user addresses a subagent by mention (e.g. `@engineer`) within a topic.
Master routes the prompt to the agent container with the specified subagent,
worktree path, and session ID.

### MQTT Communication

Mosquitto is the message bus between master and agents.

Topic namespace:

```
codex-slack/workspace/{workspace_id}/topic/{topic_id}/prompt
  payload: {
    "message_id": "<uuid>",
    "subagent": "engineer",
    "worktree": "/workspace/worktrees/<topic-id>",
    "session_id": "<llm-session-id>",
    "text": "...",
    "attachments": [...]
  }

codex-slack/workspace/{workspace_id}/topic/{topic_id}/status
  payload: {"state": "thinking"} | {"state": "idle"}

codex-slack/workspace/{workspace_id}/topic/{topic_id}/response
  payload: {
    "message_id": "<uuid>",
    "reply_to": "<prompt-message-id>",
    "last_response": "...",
    "transcript": "...",
    "session_id": "<llm-session-id>"
  }
```

Master publishes to `prompt`. Agent subscribes to `prompt`, publishes to
`status` (immediately on receipt) and `response` (when the LLM turn completes).
Master subscribes to `status` and `response`, bridges both to the browser over
WebSocket.

QoS level 1 (at-least-once) for prompts and responses. QoS 0 for status.
No retained messages on `status` (stale thinking state must not persist across
reconnects).

### WebSocket (Browser ↔ Master)

Browser connects to `ws://master/ws/{topic_id}` on page load for a topic.
Master pushes:

- `{"type": "status", "state": "thinking" | "idle"}` — forwarded from MQTT
- `{"type": "message", "sender": "agent", "last_response": "...", "transcript": "..."}` — forwarded from MQTT
- `{"type": "message", "sender": "user", "text": "..."}` — echoed on submit

Browser sends user prompts over HTTP POST (not over WebSocket) to keep request
tracking and error handling simple.

### Frontend (Vue 3 + Vite)

The frontend is a Vue 3 single-page application built with Vite. The compiled
static bundle is baked into the master image at build time and served by
FastAPI. No Node.js runs in the master container at runtime.

Primary views:
- *Workspace list* — create workspace (repo URL), list workspaces
- *Workspace detail* — topic list, create topic, agent configuration
- *Topic chat* — message thread, thinking spinner, @mention input with
  subagent autocomplete, folded transcript sections

### Data Model (SQLite)

Database file: `/opt/codex-slack/data/master/master_data.db` on master's durable volume (mounted as `master_data` Docker volume at `/opt/codex-slack/data/master`).

```
workspaces
  id            TEXT PRIMARY KEY
  name          TEXT NOT NULL UNIQUE
  repo_url      TEXT NOT NULL
  container_name TEXT
  created_at    TEXT NOT NULL
  archived_at   TEXT            -- set on soft-delete; null when active

workspace_agents
  id            TEXT PRIMARY KEY
  workspace_id  TEXT NOT NULL REFERENCES workspaces(id)
  agent_name    TEXT NOT NULL   -- the @mention name, e.g. "claude", "engineer"
  adapter       TEXT NOT NULL   -- "claude-code" | "codex"
  subagent      TEXT            -- --agent flag value for claude-code; null = no flag
  active        INTEGER NOT NULL DEFAULT 1  -- 1 = active, 0 = soft-deleted
  created_at    TEXT NOT NULL
  deleted_at    TEXT            -- set on soft-delete; null when active
  UNIQUE (workspace_id, agent_name)

topics
  id            TEXT PRIMARY KEY
  workspace_id  TEXT NOT NULL REFERENCES workspaces(id)
  subject       TEXT NOT NULL
  branch_name   TEXT NOT NULL
  worktree_path TEXT NOT NULL
  created_at    TEXT NOT NULL
  archived_at   TEXT            -- set on soft-delete; null when active

sessions
  id            TEXT PRIMARY KEY
  topic_id      TEXT NOT NULL REFERENCES topics(id)
  agent_name    TEXT NOT NULL
  adapter       TEXT NOT NULL
  llm_session_id TEXT          -- claude session id or codex session dir name
  updated_at    TEXT NOT NULL

messages
  id            TEXT PRIMARY KEY
  topic_id      TEXT NOT NULL REFERENCES topics(id)
  sender        TEXT NOT NULL  -- "user" | "agent" | "system"
  agent_name    TEXT           -- set when sender = "agent"
  text          TEXT NOT NULL
  transcript    TEXT           -- full LLM transcript; set when sender = "agent"
  attachments_json TEXT        -- JSON array of attachment metadata
  created_at    TEXT NOT NULL
```

### Staff Agent Configuration

`workspace_agents` is a configuration table — it defines which named staff
agents exist in a workspace and how master routes a prompt to each one.

*Soft delete:* Removing a staff agent sets `active = 0` and `deleted_at` to
the current timestamp rather than deleting the row. Existing `sessions` rows
that reference `agent_name` are preserved (historical record). If the same
`agent_name` is re-added later, master reactivates the existing row (sets
`active = 1`, clears `deleted_at`) rather than inserting a duplicate. The
UNIQUE constraint on `(workspace_id, agent_name)` always applies.

*Default agents:* Two rows are inserted automatically when a workspace is
created:

| agent_name | adapter | subagent | purpose |
|---|---|---|---|
| `claude` | `claude-code` | null | default Claude Code agent, no subagent flag |
| `codex` | `codex` | null | default Codex agent |

These defaults let users start working immediately after workspace creation
without any additional configuration.

*Adapter routing:*

| adapter | subagent column | CLI invocation |
|---|---|---|
| `claude-code` | null | `claude --print --verbose --output-format stream-json --dangerously-skip-permissions [--resume <id>]` |
| `claude-code` | `"engineer"` | `claude --print --verbose --output-format stream-json --dangerously-skip-permissions --agent engineer [--resume <id>]` |
| `codex` | null | `codex exec --json --dangerously-bypass-approvals-and-sandbox -s danger-full-access --ephemeral -o <tempfile> [-m <model>] <prompt>` |

*Agent discovery:* Additional staff agents beyond the two defaults can be
registered in two ways:
1. Via the frontend UI — operator enters name, adapter, and optional subagent.
2. Via agent container command — running `claude --agents` (or equivalent)
   inside the container lists available subagents defined in the project
   CLAUDE.md. Master can expose a management action that executes this command,
   parses the output, and upserts rows into `workspace_agents`. This allows
   operators to populate the full subagent list without manual entry.

### Session Persistence

*Claude Code:* First turn in a topic runs `claude --print --verbose --output-format stream-json --dangerously-skip-permissions <prompt>` without `--resume`. The `result` event in the stream-json output carries `session_id`, which master writes to the `sessions` table. Subsequent turns pass `--resume <session_id>`. If the session has expired (`No conversation found with session ID` in the output), the agent automatically retries without `--resume` and stores the new session ID. The `--verbose` flag is required — without it, `stream-json` format does not emit the `result` event.

*Codex:* Runs `codex exec --json --dangerously-bypass-approvals-and-sandbox -s danger-full-access --ephemeral -o <tempfile> [-m <model>] <prompt>`. The `--ephemeral` flag runs each turn without a persistent Codex project context. The `CODEX_HOME` directory (mounted as `codex-codex-{workspace_id}`) preserves config and auth across restarts. The `sessions` table records `llm_session_id = NULL` for Codex entries (Codex does not expose a resumable session ID via its CLI flags).

### Topic Lifecycle

1. User creates topic (subject + optional branch name)
2. Master creates feature branch off workspace default branch
3. Master creates git worktree at `/workspace/worktrees/<topic-id>/` inside the
   workspace volume
4. Master records topic in SQLite
5. User mentions `@engineer` in the topic chat
6. Master looks up the `engineer` agent record for the workspace
7. Master checks `sessions` for an existing session; creates one if absent
8. Master publishes MQTT prompt with subagent, worktree path, session ID
9. Agent receives prompt, executes LLM turn, publishes status + response
10. Master persists message in SQLite, forwards to browser over WebSocket

### Workspace Lifecycle

1. User creates workspace (repo URL) in the frontend
2. Master clones repo to workspace volume
3. Master creates agent container for the workspace
4. Master registers workspace in SQLite
5. Operator configures staff agents via the frontend (name, adapter, subagent)
6. User creates topics and starts working

Workspace teardown removes the agent container, worktrees, and SQLite records.
The workspace volume is retained unless explicitly deleted (same policy as v2).

## Alternatives Considered

See [ADR 0005](../decisions/0005-v3-system-architecture.md) for the full
alternatives analysis.

## Open Questions

- Codex session key mechanism — deferred to implementation phase.
- MQTT authentication — Mosquitto runs on the internal compose network,
  local access only; no authentication required for v3.0.
- Worktree cleanup policy — worktree is removed when its topic is archived.
- Attachment storage for the web frontend — uploaded files are stored on a
  separate durable shared volume mounted by both master and agent containers.
  Migration to S3-compatible object storage is a future option.
