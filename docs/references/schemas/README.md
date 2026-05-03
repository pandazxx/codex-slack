# Schemas

## SQLite Database Schema (v3)

Database file: `/opt/codex-slack/data/master/master_data.db`

### workspaces

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `name` | TEXT NOT NULL UNIQUE | Display name |
| `repo_url` | TEXT NOT NULL | Git remote URL |
| `container_name` | TEXT | Set after agent container is spawned; `codex-agent-{id}` |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC timestamp |
| `archived_at` | TEXT | Set on soft-delete; `NULL` when active |

Active filter: `WHERE archived_at IS NULL`

### workspace_agents

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `workspace_id` | TEXT NOT NULL | FK → workspaces(id) |
| `agent_name` | TEXT NOT NULL | @mention name, e.g. `claude`, `engineer` |
| `adapter` | TEXT NOT NULL | `claude-code` or `codex` |
| `subagent` | TEXT | Optional `--agent <subagent>` flag for claude-code; NULL = no flag |
| `active` | INTEGER NOT NULL DEFAULT 1 | 1 = active, 0 = soft-deleted |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC timestamp |
| `deleted_at` | TEXT | Set on soft-delete; NULL when active |
| UNIQUE | (workspace_id, agent_name) | |

Active filter: `WHERE active = 1`

Default agents inserted on workspace creation: `claude` (claude-code) and `codex` (codex).

### topics

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `workspace_id` | TEXT NOT NULL | FK → workspaces(id) |
| `subject` | TEXT NOT NULL | User-supplied subject line |
| `branch_name` | TEXT NOT NULL | Git branch for the topic's worktree |
| `worktree_path` | TEXT NOT NULL | Path inside agent container: `/workspace/worktrees/{id}` |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC timestamp |
| `archived_at` | TEXT | Set on soft-delete; NULL when active |

Active filter: `WHERE archived_at IS NULL`

### sessions

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `topic_id` | TEXT NOT NULL | FK → topics(id) |
| `agent_name` | TEXT NOT NULL | Matches `workspace_agents.agent_name` |
| `adapter` | TEXT NOT NULL | `claude-code` or `codex` |
| `llm_session_id` | TEXT | Claude session ID from stream-json `result` event; NULL for codex or before first turn |
| `updated_at` | TEXT NOT NULL | ISO-8601 UTC timestamp of last update |

### messages

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID |
| `topic_id` | TEXT NOT NULL | FK → topics(id) |
| `sender` | TEXT NOT NULL | `user`, `agent`, or `system` |
| `agent_name` | TEXT | Set when sender = `agent` |
| `text` | TEXT NOT NULL | Message text (user prompt or agent last_response) |
| `transcript` | TEXT | JSON-encoded array of stream-json events; set on agent messages |
| `attachments_json` | TEXT | JSON array of attachment metadata (currently always NULL) |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC timestamp |
