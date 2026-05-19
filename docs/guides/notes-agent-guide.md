# Notes Agent Guide

This guide is intended to be pasted into an agent's **system prompt**. It teaches the agent how to create, read, update, and delete notes at both workspace and topic scope.

## MCP tools (preferred)

When running inside a codex-slack agent container the `notes` MCP server is
available. Prefer these tools over direct REST calls — they handle
authentication and endpoint resolution automatically.

### Always available (workspace scope)

| Tool | Purpose |
|---|---|
| `list_workspace_notes(tag?)` | List all workspace notes; pass `tag` to filter (e.g. `"memory"`) |
| `get_workspace_note(key)` | Fetch a single workspace note by key |
| `create_workspace_note(key, value, tags?)` | Create a new workspace note |
| `update_workspace_note(key, value?, tags?)` | Update value and/or tags of an existing note |
| `delete_workspace_note(key)` | Delete a workspace note |

### Topic scope

Topic tools are always registered and require `topic_id` as the first argument. Pass the value of the `TOPIC_ID` environment variable.

| Tool | Purpose |
|---|---|
| `list_topic_notes(topic_id, tag?)` | List notes for the given topic |
| `create_topic_note(topic_id, key, value, tags?)` | Create a note scoped to a topic |
| `update_topic_note(topic_id, key, value?, tags?)` | Update a topic note |
| `delete_topic_note(topic_id, key)` | Delete a topic note |

Example: `list_topic_notes(topic_id=$TOPIC_ID)`.

**Session start:** always call `list_workspace_notes(tag="memory")` first to
recall persistent context before replying.

---

## REST API (fallback)

Use the REST API only if MCP tools are unavailable. Replace `{BASE}` with
`$MASTER_URL/api`, `{WID}` with `$WORKSPACE_ID`, `{TID}` with `$TOPIC_ID`.

---

## What notes are

Notes are key-value pairs with optional tags stored in the workspace. They exist at two scopes:

- **Workspace notes** — shared across all topics in the workspace.
- **Topic notes** — scoped to a single topic; not visible to other topics.

Each note has:

| Field | Description |
|---|---|
| `key` | Unique slug (e.g. `project-goal`). Immutable after creation. |
| `value` | Freeform text. Any length. |
| `tags` | List of strings used for filtering (e.g. `["memory", "context"]`). |

Notes tagged `memory` are injected into prompts using one of two markers:

- `{ws:note:notes:memory}` — injects each matching note as a `key: value` line
- `{ws:note:keys:memory}` — injects only the key names, one per line

---

## API reference

Replace `{BASE}` with the API root (e.g. `http://localhost:8000/api`), `{WID}` with the workspace ID, and `{TID}` with the topic ID.

### Workspace notes

| Operation | Method | Path |
|---|---|---|
| List all | `GET` | `/workspaces/{WID}/notes` |
| Get one | `GET` | `/workspaces/{WID}/notes/{key}` |
| Create | `POST` | `/workspaces/{WID}/notes` |
| Update | `PATCH` | `/workspaces/{WID}/notes/{key}` |
| Delete | `DELETE` | `/workspaces/{WID}/notes/{key}` |

### Topic notes

| Operation | Method | Path |
|---|---|---|
| List all | `GET` | `/workspaces/{WID}/topics/{TID}/notes` |
| Get one | `GET` | `/workspaces/{WID}/topics/{TID}/notes/{key}` |
| Create | `POST` | `/workspaces/{WID}/topics/{TID}/notes` |
| Update | `PATCH` | `/workspaces/{WID}/topics/{TID}/notes/{key}` |
| Delete | `DELETE` | `/workspaces/{WID}/topics/{TID}/notes/{key}` |

---

## Request and response shapes

### Create (POST)

```json
{
  "key": "project-goal",
  "value": "Build a multi-agent AI assistant integrated with Slack.",
  "tags": ["memory", "context"]
}
```

- `key` is required and must be unique within the scope. Returns `409 Conflict` if already exists.
- `tags` is optional; defaults to `[]`.

### Update (PATCH)

```json
{
  "value": "Updated text here.",
  "tags": ["memory"]
}
```

- `key` cannot be changed. Only `value` and/or `tags` may be patched.
- Either field may be omitted to leave it unchanged.

### Response (all reads and writes return the same shape)

```json
{
  "key": "project-goal",
  "value": "Build a multi-agent AI assistant integrated with Slack.",
  "tags": ["memory", "context"],
  "scope_type": "workspace",
  "scope_id": "3bba2a2d-5d7e-468c-85a8-20cddc1db57d",
  "created_at": "2026-05-15T09:00:00",
  "updated_at": "2026-05-15T09:00:00"
}
```

---

## Worked examples

### List all workspace notes

```
GET /api/workspaces/3bba2a2d-5d7e-468c-85a8-20cddc1db57d/notes
```

### Create a workspace note

```
POST /api/workspaces/3bba2a2d-5d7e-468c-85a8-20cddc1db57d/notes
Content-Type: application/json

{
  "key": "communication-style",
  "value": "Be concise. Use bullet points. Avoid jargon.",
  "tags": ["memory", "style"]
}
```

### Update a workspace note

```
PATCH /api/workspaces/3bba2a2d-5d7e-468c-85a8-20cddc1db57d/notes/communication-style
Content-Type: application/json

{
  "value": "Be concise. Use bullet points. Avoid jargon. When uncertain, say so."
}
```

### Delete a workspace note

```
DELETE /api/workspaces/3bba2a2d-5d7e-468c-85a8-20cddc1db57d/notes/communication-style
```

Returns `204 No Content` on success.

### Create a topic note

```
POST /api/workspaces/3bba2a2d-5d7e-468c-85a8-20cddc1db57d/topics/d6017811-ae27-45e3-b589-542e1771f590/notes
Content-Type: application/json

{
  "key": "topic-objective",
  "value": "Summarise daily standups and post a digest to #standup by 09:30.",
  "tags": ["memory", "context"]
}
```

---

## How to obtain workspace and topic IDs

If the user does not supply IDs explicitly, you can discover them:

```
GET /api/workspaces          → list of all workspaces (id, name, …)
GET /api/workspaces/{WID}/topics  → list of topics in a workspace (id, name, …)
```

Match by name if the user refers to a workspace or topic by name rather than ID.

---

## Error codes

| Code | Meaning |
|---|---|
| `409 Conflict` | A note with that key already exists in this scope. Use PATCH to update it. |
| `404 Not Found` | No note with that key in this scope. |
| `422 Unprocessable Entity` | Request body failed validation (missing required field, wrong type, etc.). |

---

## Behavioral rules for the agent

1. **Never guess a key.** If asked to update or delete a note and unsure of the exact key, list notes first and confirm with the user.
2. **Prefer PATCH over delete-and-recreate.** If a note exists (409 on create), switch to PATCH on the same key.
3. **Scope explicitly.** Always clarify whether the user means workspace-level or topic-level before creating. Workspace notes are visible across all topics; topic notes are private to one topic.
4. **Tags are searchable.** When creating notes the user intends to inject into prompts, suggest the `memory` tag. Use `{ws:note:notes:memory}` for key: value pairs or `{ws:note:keys:memory}` for keys only.
5. **Confirm before deleting.** Deletion is permanent. Confirm the key and scope with the user before issuing a DELETE.
