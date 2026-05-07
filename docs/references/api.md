# API Reference

This page documents the REST API, WebSocket interface, and MQTT topic patterns for the v3 codex-slack system.

## REST API

The master service exposes a REST API at `http://master:8080`. All data endpoints are prefixed with `/api/`. The Vue 3 SPA is served at the root.

### Workspaces

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspaces` | List active workspaces. Pass `?archived=true` to list archived workspaces instead. |
| `POST` | `/api/workspaces` | Create a workspace. Returns `201` with the new workspace object. Returns `409` if the name already exists. |
| `GET` | `/api/workspaces/{id}` | Get a workspace by ID (works for both active and archived). |
| `DELETE` | `/api/workspaces/{id}` | Soft-delete a workspace: sets `archived_at`, cascades to all active topics, and stops the agent container. Returns `204`. |

**POST /api/workspaces — request body:**

```json
{
  "name": "my-workspace",
  "repo_url": "https://github.com/org/repo",
  "repo_ref": "master"
}
```

**Workspace response shape:**

```json
{
  "id": "<uuid>",
  "name": "my-workspace",
  "repo_url": "https://github.com/org/repo",
  "container_name": "codex-agent-<workspace-id>",
  "created_at": "2026-05-01T12:00:00Z",
  "archived_at": null,
  "agents": [
    { "id": "<uuid>", "agent_name": "claude", "adapter": "claude-code", "subagent": null, "active": true },
    { "id": "<uuid>", "agent_name": "codex",  "adapter": "codex",       "subagent": null, "active": true }
  ]
}
```

### Topics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspaces/{id}/topics` | List active topics. Pass `?archived=true` to list archived topics. |
| `POST` | `/api/workspaces/{id}/topics` | Create a topic. Returns `201`. |
| `GET` | `/api/workspaces/{id}/topics/{tid}` | Get a topic by ID (works for archived too). |
| `DELETE` | `/api/workspaces/{id}/topics/{tid}` | Soft-delete a topic: sets `archived_at`. Returns `204`. |

**POST /api/workspaces/{id}/topics — request body:**

```json
{
  "subject": "Fix login bug",
  "branch_name": "fix-login-bug"
}
```

`branch_name` is optional — omitting it auto-generates a slug from the subject.

**Topic response shape:**

```json
{
  "id": "<uuid>",
  "workspace_id": "<uuid>",
  "subject": "Fix login bug",
  "branch_name": "fix-login-bug",
  "worktree_path": "/workspace/worktrees/<topic-id>",
  "created_at": "2026-05-01T12:00:00Z",
  "archived_at": null
}
```

### Messages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspaces/{id}/topics/{tid}/messages` | List all messages in a topic, ordered by `created_at`. |
| `POST` | `/api/workspaces/{id}/topics/{tid}/messages` | Send a user message and dispatch a prompt to the agent via MQTT. Returns `202` with `{"message_id": "...", "status": "queued"}`. |

**POST …/messages — request body:**

```json
{
  "text": "@claude fix the typo in README",
  "agent_name": "claude"
}
```

`agent_name` sets the default routing target. An `@mention` prefix in `text` (e.g. `@codex`) overrides `agent_name`.

**Message response shape (GET):**

```json
{
  "id": "<uuid>",
  "sender": "user" | "agent" | "system",
  "agent_name": "claude",
  "text": "...",
  "transcript": "...",
  "created_at": "2026-05-01T12:00:00Z"
}
```

`transcript` is a JSON-encoded array of stream-json events (set on agent messages only).

### Workspace Agents

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspaces/{id}/agents` | List active (non-deleted) agents for a workspace. |
| `POST` | `/api/workspaces/{id}/agents` | Add a named agent configuration. Returns `201`. Returns `409` if an active agent with that name exists. Re-activates a soft-deleted agent of the same name. |
| `DELETE` | `/api/workspaces/{id}/agents/{agent_id}` | Soft-delete an agent configuration (sets `active=0`, records `deleted_at`). Returns `204`. |

**POST …/agents — request body:**

```json
{
  "agent_name": "engineer",
  "adapter": "claude-code",
  "subagent": null
}
```

Valid adapters: `"claude-code"`, `"codex"`.

**Agent response shape:**

```json
{
  "id": "<uuid>",
  "agent_name": "engineer",
  "adapter": "claude-code",
  "subagent": null,
  "active": true
}
```

### Utility Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check. Returns `{"status": "ok"}`. |
| `GET` | `/schema` | Returns the DB schema (column names per table) for debugging. |

## WebSocket Interface

The browser connects to `ws://master:8080/ws/events` — a single global channel. The frontend filters events by `topic_id` where relevant.

**Messages pushed by master to browser:**

```json
{ "type": "status", "state": "thinking" | "idle" }
{ "type": "message", "sender": "agent", "last_response": "...", "transcript": "...", "agent_name": "claude", "session_id": "<sid>" }
{ "type": "chunk", "topic_id": "<uuid>", "message_id": "<uuid>", "agent_name": "claude", "seq": 7, "event": { ...stream-json event... } }
{ "type": "chunk_replay", "topic_id": "<uuid>", "message_id": "<uuid>", "agent_name": "claude", "events": [ ...ordered stream-json events... ] }
```

- **`chunk`** — one frame per Claude stdout line published while the agent is streaming. The frontend appends to a live message bubble keyed by `message_id`.
- **`chunk_replay`** — sent once per in-progress `message_id` immediately after a client connects (before any further live `chunk` frames). Replays all chunks the master has persisted so far for that stream, enabling browser-refresh recovery mid-stream.
- **`message`** — the durable agent reply. Replaces the live `chunk` placeholder for the matching `message_id` and clears the `chunks` table rows for that id.
- **`status`** — coarse agent lifecycle signal (thinking / idle).

User messages are sent via `POST /api/.../messages` (HTTP, not WebSocket). The WebSocket is receive-only from the browser's perspective.

## MQTT Topic Patterns

Mosquitto is the message bus between master and agent containers.

```
codex-slack/workspace/{workspace_id}/topic/{topic_id}/prompt
  direction: master → agent
  QoS: 1
  payload: {
    "message_id": "<uuid>",
    "agent_name": "claude",
    "adapter": "claude-code",
    "subagent": null,
    "worktree": "/workspace/worktrees/<topic-id>",
    "branch": "fix-login-bug",
    "session_id": "<llm-session-id>" | null,
    "text": "...",
    "attachments": []
  }

codex-slack/workspace/{workspace_id}/topic/{topic_id}/status
  direction: agent → master
  QoS: 0 (no retained messages)
  payload: { "state": "thinking" } | { "state": "idle" }

codex-slack/workspace/{workspace_id}/topic/{topic_id}/response
  direction: agent → master
  QoS: 1
  payload: {
    "message_id": "<uuid>",
    "agent_name": "claude",
    "reply_to": "<prompt-message-id>",
    "last_response": "...",
    "transcript": "<json-array-of-stream-json-events>",
    "session_id": "<new-llm-session-id>"
  }
```
