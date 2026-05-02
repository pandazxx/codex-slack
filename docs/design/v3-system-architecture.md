# v3.0 System Architecture

*Status:* proposed
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
  - MQTT client
      - subscribes to prompt messages for its workspace
      - publishes response and status messages
  - LLM session manager
      - one Claude Code session per topic (--resume <id>)
      - one Codex session per topic (CODEX_HOME/sessions/<id>)
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

Database file: `/data/master/master_data.db` on master's durable volume.

```
workspaces
  id            TEXT PRIMARY KEY
  name          TEXT NOT NULL
  repo_url      TEXT NOT NULL
  container_name TEXT
  created_at    TEXT NOT NULL

workspace_agents  -- NOTE: detailed design TBD; further discussion needed
  id            TEXT PRIMARY KEY
  workspace_id  TEXT NOT NULL REFERENCES workspaces(id)
  agent_name    TEXT NOT NULL   -- e.g. "engineer", "tester"
  adapter       TEXT NOT NULL   -- "claude-code" | "codex"
  subagent      TEXT            -- claude-code subagent flag value

topics
  id            TEXT PRIMARY KEY
  workspace_id  TEXT NOT NULL REFERENCES workspaces(id)
  subject       TEXT NOT NULL
  branch_name   TEXT NOT NULL
  worktree_path TEXT NOT NULL
  created_at    TEXT NOT NULL

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

### Session Persistence

*Claude Code:* First turn in a topic runs `claude -p --subagent <name> ...`
without `--resume`. The returned session ID is written to the `sessions` table.
Subsequent turns in the same topic pass `--resume <session-id>`. The response
payload from the agent includes the current session ID so master can update the
table if it changes.

*Codex:* Session state lives under `CODEX_HOME/sessions/<name>/`. Master stores
the session directory name (or an equivalent Codex session key) in the
`sessions` table. The agent receives it via env var `AGENT_SESSION_ID` and
passes it to the Codex invocation. Codex session creation behavior should be
verified against the Codex CLI docs during implementation.

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
