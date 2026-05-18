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
  "sender": "user" | "agent" | "event",
  "agent_name": "claude",
  "text": "...",
  "transcript": "...",
  "created_at": "2026-05-01T12:00:00Z"
}
```

`transcript` is a JSON-encoded array of stream-json events (set on agent messages only).

`sender` values: `"user"` for human-typed messages, `"agent"` for agent replies, `"event"` for messages dispatched by an event action (scheduler, archive hook, message hooks).

### Event Actions

Event actions bind in-system events to staff invocations for a specific topic. All five endpoints are scoped under the topic path. Returns `404` if the workspace or topic is not found.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspaces/{wid}/topics/{tid}/event-actions` | List all event actions for the topic, ordered by `created_at`. |
| `POST` | `/api/workspaces/{wid}/topics/{tid}/event-actions` | Create an event action. Returns `201` with the new action object. |
| `GET` | `/api/workspaces/{wid}/topics/{tid}/event-actions/{id}` | Get a single event action by ID. |
| `PATCH` | `/api/workspaces/{wid}/topics/{tid}/event-actions/{id}` | Update one or more fields. Only fields present in the request body are changed. Returns the updated action. |
| `DELETE` | `/api/workspaces/{wid}/topics/{tid}/event-actions/{id}` | Delete an event action permanently. Returns `204`. |

**POST …/event-actions — request body (`EventActionIn`):**

```json
{
  "event_type": "topic_message_sent",
  "staff_name": "reviewer",
  "prompt_template": "Review the following message: {msgbody}",
  "timing": "after",
  "cron_expr": null,
  "enabled": true,
  "structured_output": false
}
```

The `EventActionIn` model uses `extra='forbid'` — unknown fields are rejected with 422.

**PATCH …/event-actions/{id} — request body (`EventActionPatch`):**

```json
{
  "structured_output": true
}
```

Only the fields you include are changed; omitted fields are left as-is. Sending `null` for `timing` or `cron_expr` explicitly sets those fields to null (valid for event types that allow null values). Sending `null` for `staff_name`, `prompt_template`, `enabled`, or `structured_output` is rejected with 422. `event_type` cannot be changed after creation — it is not accepted in PATCH bodies (rejected with 422 by `extra='forbid'`).

**Event action response shape (`EventActionOut`):**

```json
{
  "id": "<uuid>",
  "event_type": "topic_message_sent",
  "scope_type": "topic",
  "scope_id": "<topic-uuid>",
  "staff_name": "reviewer",
  "prompt_template": "Review the following message: {msgbody}",
  "timing": "after",
  "cron_expr": null,
  "last_fired_at": null,
  "last_run_at": "2026-05-08T09:01:00Z",
  "last_run_status": "ok",
  "last_run_output": "message_id=<uuid> prompt='Review the following…'",
  "enabled": true,
  "structured_output": false,
  "created_at": "2026-05-08T09:00:00Z",
  "updated_at": "2026-05-08T09:00:00Z"
}
```

**Observability fields:**

| Field | Writer | Meaning |
|---|---|---|
| `last_fired_at` | Scheduler tick only | UTC ISO-8601 watermark of the last cron slot the scheduler accounted for. Advanced *before* dispatch. Null for non-scheduler event types. |
| `last_run_at` | Event worker (standard) or MQTT reply handler (`structured_output=true`) | UTC ISO-8601 timestamp of the most recent dispatch attempt (success or failure). When `structured_output=true`, written when the agent reply arrives via MQTT, not at dispatch time. |
| `last_run_status` | Event worker (standard) or MQTT reply handler (`structured_output=true`) | Outcome of the most recent dispatch: `ok`, `staff_missing`, `render_error`, or `dispatch_error`. |
| `last_run_output` | Event worker (standard) or MQTT reply handler (`structured_output=true`) | On `ok` (standard): rendered prompt prefix and dispatched `message_id`. On `ok` (structured): the log field from a `silent` response, or empty. On `ok` with invalid JSON reply: `invalid_json: <first 200 chars>`. On error: the error message or timeout marker. Truncated to 4096 characters. |
| `structured_output` | Set at create/patch time | Boolean (default `false`). When `true`, the staff's reply is intercepted and parsed as JSON instead of being broadcast as an agent message. |

**Event types and timing/cron_expr rules:**

| `event_type` | When it fires | `timing` | `cron_expr` |
|---|---|---|---|
| `topic_message_sent` | A user sends a message in the topic | Required: `"before"` or `"after"` | Must be null |
| `topic_message_received` | An agent reply lands in the topic | Null or `"after"` | Must be null |
| `topic_scheduler` | A cron expression matches the current time | Must be null | Required; 5-field only |
| `topic_archived` | The topic is archived | Null or `"after"` | Must be null |

For `topic_message_sent`, `"before"` fires before the user's message is dispatched to MQTT; `"after"` fires after. Both observe only — neither can modify or veto the original message.

**Cron expression rules:**

- Must be a valid 5-field cron expression (minute hour day month weekday). 6-field and `@`-shorthand expressions are rejected with 422.
- Validated using `croniter.is_valid()` at write time.
- Interpreted in the configured display timezone (`system.timezone` system setting, default OS local TZ). Cron strings are not UTC. See `/api/config/system-settings`.
- Scheduler fires at minute-level resolution (60 s background loop). Sub-minute expressions pass validation but the effective minimum interval is 1 minute.

**Template variables:**

| `event_type` | Available variables |
|---|---|
| `topic_message_sent` | `{msgbody}`, `{topic_name}`, `{message_json}`, `{topic_json}` |
| `topic_message_received` | `{msgbody}`, `{topic_name}`, `{response_json}`, `{topic_json}` |
| `topic_scheduler` | `{topic_name}`, `{workspace_name}`, `{topic_json}` |
| `topic_archived` | `{topic_name}`, `{topic_json}` |

`{msgbody}` is the raw text of the triggering message (user input for `topic_message_sent`; the agent's reply text for `topic_message_received`).

`{message_json}` (`topic_message_sent` only) — the triggering user message as a JSON string: `{"text": "...", "sender": "user"}`.

`{response_json}` (`topic_message_received` only) — the agent reply as a JSON string: `{"text": "...", "agent_name": "...", "sender": "agent"}`.

`{topic_json}` (all event types) — topic and workspace identifiers as a JSON string: `{"id": "...", "subject": "...", "workspace_id": "...", "workspace_name": "..."}`. Injected automatically by the dispatcher; the operator does not need to construct it.

Unknown placeholders are left as the literal `{name}` string and a warning is logged. Escape a literal brace with `{{` or `}}`.

**Structured output response shapes:**

When `structured_output=true`, the staff's LLM reply is intercepted and parsed as JSON rather than broadcast as an agent message. The following shapes are handled:

| Shape | Effect |
|---|---|
| `{"message": "<text>"}` | Posts `<text>` as an agent message in the topic (no further LLM call). `last_run_status` = `ok`. |
| `{"break": true, "message": "<reason>"}` | Logs the veto intent. Archive blocking is not yet implemented ([#156](https://github.com/pandazxx/codex-slack/issues/156)); `break` is acknowledged and logged. `last_run_status` = `ok`. No message posted. |
| `{"silent": true, "log": "<optional text>"}` | Suppresses any reply. `last_run_status` = `ok`. `log` text (if present) appears in `last_run_output`. |

If the reply is not valid JSON, `last_run_status` = `ok` and `last_run_output` = `invalid_json: <first 200 chars of reply>`. No message is posted.

When `structured_output=true`, `last_run_at`, `last_run_status`, and `last_run_output` are written when the agent reply arrives via MQTT, not at dispatch time. There is a delay equal to the agent's response latency before the status appears on the action.

**Validation errors returned as 422:** invalid cron expression; wrong `timing`/`cron_expr` combination for the `event_type`; null value for a non-nullable patch field; extra fields in the request body.

**Enable/disable:** PATCH `{"enabled": false}` to disable without deleting. There is no dedicated toggle endpoint.

### System Settings

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/config/system-settings` | Return the current system-level settings. |
| `PATCH` | `/api/config/system-settings` | Update system-level settings. |

**Request and response shape (`SystemSettings`):**

```json
{
  "timezone": "Asia/Shanghai"
}
```

`timezone` must be a valid IANA timezone string (validated with `zoneinfo.ZoneInfo(value)`). Invalid values are rejected with 422. If `system.timezone` has never been set, `GET` returns the OS local timezone detected via `tzlocal.get_localzone()`.

This setting controls how cron expressions in `topic_scheduler` event actions are interpreted. It also controls how cron times are displayed in the UI (next to the cron input field and in action cards). All datetimes are stored as UTC; this setting only affects interpretation and display at the edges.

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

### Notes

Notes are key-value pairs with optional tags, stored at workspace or topic scope. See `docs/guides/notes-agent-guide.md` for agent usage.

#### Workspace-scoped notes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspaces/{wid}/notes` | List all workspace notes. |
| `GET` | `/api/workspaces/{wid}/notes/{key}` | Get a single workspace note by key. Returns `404` if not found. |
| `POST` | `/api/workspaces/{wid}/notes` | Create a workspace note. Returns `201`. Returns `409` if `key` already exists in this scope. |
| `PATCH` | `/api/workspaces/{wid}/notes/{key}` | Update `value` and/or `tags` of an existing workspace note. `key` is immutable. Returns the updated note. |
| `DELETE` | `/api/workspaces/{wid}/notes/{key}` | Delete a workspace note permanently. Returns `204`. |

#### Topic-scoped notes

Same endpoints, prefixed under the topic path:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/workspaces/{wid}/topics/{tid}/notes` | List all notes for a topic. |
| `GET` | `/api/workspaces/{wid}/topics/{tid}/notes/{key}` | Get a single topic note by key. |
| `POST` | `/api/workspaces/{wid}/topics/{tid}/notes` | Create a topic note. Returns `201`. Returns `409` if `key` already exists in this topic scope. |
| `PATCH` | `/api/workspaces/{wid}/topics/{tid}/notes/{key}` | Update `value` and/or `tags`. `key` is immutable. |
| `DELETE` | `/api/workspaces/{wid}/topics/{tid}/notes/{key}` | Delete a topic note. Returns `204`. |

**POST …/notes — request body (`NoteIn`):**

```json
{
  "key": "project-goal",
  "value": "Ship v1 by end of Q2.",
  "tags": ["memory", "context"]
}
```

`key` must be a unique URL-safe slug within the scope. `tags` defaults to `[]`.

**PATCH …/notes/{key} — request body (`NotePatch`):**

```json
{
  "value": "Updated text.",
  "tags": ["memory"]
}
```

Either field may be omitted to leave it unchanged. `key` is not accepted (rejected with 422).

**Note response shape (`NoteOut`):**

```json
{
  "key": "project-goal",
  "value": "Ship v1 by end of Q2.",
  "tags": ["memory", "context"],
  "scope_type": "workspace",
  "scope_id": "<workspace-or-topic-uuid>",
  "created_at": "2026-05-15T09:00:00",
  "updated_at": "2026-05-15T09:00:00"
}
```

All write endpoints (POST, PATCH) return the note object. DELETE returns `204 No Content`.

**Prompt injection markers:** workspace notes tagged with a given tag can be injected into staff system prompts and event-action templates using `{ws:note:notes:<tag>}` (key: value lines) or `{ws:note:keys:<tag>}` (key names only). See `docs/design/notes.md` for details.

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
