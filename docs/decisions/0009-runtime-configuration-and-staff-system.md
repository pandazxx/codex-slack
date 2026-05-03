# 0009 Runtime Configuration and Staff System

- Status: proposed
- Date: 2026-05-03
- Issues: [#90](https://github.com/pandazxx/codex-slack/issues/90) [#91](https://github.com/pandazxx/codex-slack/issues/91) [#92](https://github.com/pandazxx/codex-slack/issues/92)

## Context

The current system requires all agent configuration to be supplied at deployment
time as environment variables in `docker-compose.yml`: API keys, git identity,
model choices, and which adapter (claude-code, codex) to use per workspace.
There is no way to change these after the service starts without editing the
compose file and restarting containers.

Two categories of configuration need to move to runtime:

**1. Operational settings** — API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, etc.),
git identity (`GIT_USER_NAME`, `GIT_USER_EMAIL`), and arbitrary container env
vars that currently must be baked into the compose file. Operators should be
able to configure these through a settings UI after the service is up, similar
to the Jenkins initial-setup wizard.

**2. Agent invocation profiles** — The existing `agents` table records only
which adapter (claude-code, codex) is active per workspace. There is no way to
configure per-workspace or per-conversation model selection, system prompts, or
the `--agent` flag passed to `claude-code`. Power users need named, reusable
invocation profiles they can summon by @mention in a chat topic.

These two needs share a common requirement: a hierarchical, runtime-editable
configuration store.

## Decision

### 1. Introduce the "Staff" concept to replace the `agents` table

A **Staff** is a named invocation profile for a CLI agent adapter. It bundles
all the flags needed to call `claude-code` (or another adapter) into a
addressable unit that a user can summon with `@name` in a topic message.

A Staff record holds:

| Field | Type | Description |
|-------|------|-------------|
| `name` | TEXT | @mention trigger, unique within its scope |
| `adapter` | TEXT | `claude-code` \| `codex` |
| `model` | TEXT | `--model` flag (NULL = adapter default) |
| `system_prompt` | TEXT | `--append-system-prompt` value |
| `agent` | TEXT | `--agent` flag (sub-agent name within the adapter) |
| `session_scope` | TEXT | `topic` \| `workspace` \| `global` |
| `is_default` | BOOL | one default per scope; used when no @mention is present |
| `extra_flags` | TEXT | JSON bag for future CLI flags |

The existing `agents` table is replaced by `staffs`. A migration creates one
default workspace-scoped Staff for each existing workspace agent row, then
drops `agents`.

### 2. Three-level cascade hierarchy for Staff

Staff definitions exist at three scopes:

```
global
  └── workspace   (overrides global for a specific project)
        └── topic (overrides workspace for a specific conversation)
```

Resolution when `@name` is used in topic T belonging to workspace W:

1. Look for Staff named `name` where `scope_type='topic'` and `scope_id=T.id`
2. If not found, look where `scope_type='workspace'` and `scope_id=W.id`
3. If not found, look where `scope_type='global'`
4. If not found, send an error reply to the user; do not fall through to the
   default agent

**Override semantics**: a match at a lower scope replaces the entire record from
a higher scope — no field-by-field merging. This keeps resolution simple and
predictable: you always know exactly which record is in effect.

**Default routing** (no @mention in message): same cascade, filtering on
`is_default=TRUE`. At least one default Staff must exist in the effective scope
chain, or messages with no @mention will receive an error reply.

### 3. Session ID derived from Staff and session_scope

Rather than storing session IDs in a separate table, they are computed
deterministically from the Staff's scope key:

| `session_scope` | Session ID source |
|-----------------|-------------------|
| `topic` | `{workspace_id}:{topic_id}:{staff_name}` |
| `workspace` | `{workspace_id}:{staff_name}` |
| `global` | `{staff_name}` |

The source string is hashed (SHA-1, hex) and used as the `--resume` argument
to `claude-code`. A Staff with `session_scope=workspace` therefore accumulates
context across all topics in that workspace, giving it persistent memory of
prior conversations — appropriate for long-running reviewer or research roles.

### 4. Introduce a `config` table for operational settings

Operational key-value settings (API keys, git identity, env vars) are stored in
a separate `config` table. This is deliberately distinct from `staffs` — config
affects the container runtime process; staffs affect LLM invocation.

```
config
  scope_type  TEXT  -- 'global' | 'workspace'
  scope_id    TEXT  -- NULL for global, workspace_id for workspace
  key         TEXT
  value       TEXT
  updated_at  TEXT
  PRIMARY KEY (scope_type, scope_id, key)
```

Config has two scopes only (global and workspace — no topic-level env vars).
Resolution: workspace value wins over global for the same key. At agent
container startup, the resolved config is merged with the environment, with
config values taking precedence over deployment-time env vars.

Config changes apply on next container start — there is no live env injection.

### 5. Staff list endpoint returns inherited records with provenance

`GET /api/workspaces/{wid}/staffs` returns all Staff records visible in that
workspace: both workspace-defined and inherited global staffs. Each record
includes an `inherited_from` field (`null` for local, `"global"` for
inherited), allowing the UI to display source badges without a second request.

The same pattern applies to `GET /api/workspaces/{wid}/topics/{tid}/staffs`.

### 6. API surface

```
# Staff — global scope
GET    /api/staffs
POST   /api/staffs
PUT    /api/staffs/{name}
DELETE /api/staffs/{name}

# Staff — workspace scope
GET    /api/workspaces/{wid}/staffs
POST   /api/workspaces/{wid}/staffs
PUT    /api/workspaces/{wid}/staffs/{name}
DELETE /api/workspaces/{wid}/staffs/{name}

# Staff — topic scope
GET    /api/workspaces/{wid}/topics/{tid}/staffs
POST   /api/workspaces/{wid}/topics/{tid}/staffs
PUT    /api/workspaces/{wid}/topics/{tid}/staffs/{name}
DELETE /api/workspaces/{wid}/topics/{tid}/staffs/{name}

# Config — global scope
GET    /api/config
PATCH  /api/config          -- upsert/delete keys

# Config — workspace scope
GET    /api/workspaces/{wid}/config
PATCH  /api/workspaces/{wid}/config
```

`GET` on a scoped config endpoint returns the merged view (workspace over
global), not the raw workspace-only values, to give the UI a single source of
truth for what is actually in effect.

### 7. @mention parsing and routing in master

When a message arrives at
`POST /api/workspaces/{wid}/topics/{tid}/messages`, master:

1. Parses the message text for the first `@word` token.
2. If found, resolves the Staff (topic → workspace → global) and routes to it.
3. If not found, routes to the default Staff in the effective scope.
4. If resolution fails at either step, returns an error message to the topic
   rather than silently dropping the message or using an unexpected fallback.

Only the first @mention is acted on per message. Multiple @mentions in a single
message are not supported in v1.

### 8. Settings UI

Two new UI surfaces:

- **`/settings`** — global scope: global Staff list + global config (API keys,
  git identity, custom env vars). This is the initial-setup panel referenced in
  #90.
- **Workspace detail page — Agents section** — workspace-scoped Staff list,
  replacing the current static agent display. Users can create, edit, and delete
  workspace staffs and see inherited global staffs with a badge.

Topic-scoped Staff management is deferred to a future slice — the backend
supports it, but no topic-level Staff UI is built now.

## Alternatives Considered

### A. Field-by-field merge instead of full override

A lower-level Staff definition could override only the fields it specifies,
inheriting the rest from the parent scope. This is more flexible (e.g., override
only `model` at topic level without repeating the system prompt).

Rejected for v1. Merge semantics make it hard to reason about what is actually
in effect — particularly when debugging unexpected model or prompt behavior.
Full override keeps each effective Staff record self-contained. Merge can be
introduced later if the use cases demand it.

### B. Keep the `agents` table alongside `staffs`

The `agents` table could continue to serve as the "enabled adapters" registry
while `staffs` handles invocation profiles.

Rejected. Staffs subsume agents entirely — a Staff record contains the adapter
field. Keeping both tables creates a redundant concept, two code paths to
maintain, and a confusing mental model. A migration is cleaner.

### C. Store session IDs in a dedicated `staff_sessions` table

A sessions table would allow the UI to list active sessions, clear a session
explicitly, and store session metadata.

Deferred. Deterministic session ID generation covers the immediate need without
schema overhead. A sessions table can be added when explicit session management
(listing, clearing, inspecting) is required.

### D. Topic-level config (env vars)

Allow env var overrides at topic scope, matching the Staff hierarchy depth.

Rejected. Env vars affect container startup — they cannot be applied per-topic
without launching a separate container per topic, which is out of scope. Config
stays at workspace + global only.

### E. Encrypt secrets at rest

Store API keys and tokens encrypted in the `config` table.

Deferred. The threat model for a self-hosted single-user deployment does not
require encryption at rest in v1. The UI will display a "stored as plain text"
disclaimer for sensitive keys. Encryption can be layered in later without schema
changes (values can be prefixed with an encryption header).

## Consequences

**Positive:**

- Operators can configure the system after initial deploy with no compose file
  edits or container restarts for most settings.
- Staff gives users a first-class, named way to customize model, prompt, and
  sub-agent selection per project or per conversation.
- Persistent sessions (via `session_scope=workspace`) allow long-running
  specialist roles to accumulate project-specific context naturally.
- The `inherited_from` field in list responses makes the cascade transparent in
  the UI without extra API calls.

**Tradeoffs:**

- Migration from `agents` to `staffs` is a one-way, destructive schema change.
  Existing deployments must run the migration before the new code; rolling back
  is manual.
- Config changes to env vars (API keys) require a container restart to take
  effect. The UI must make this explicit to avoid user confusion.
- Full-override cascade means users must copy the full Staff record if they want
  to change a single field at a lower scope. This is acceptable for v1 given the
  simplicity gain.
- Plain-text secret storage requires operators to treat the SQLite file as a
  sensitive asset.

## Implementation Guidance

This work should be split into three parallel tracks after the ADR is accepted:

**Track 1 — Backend (engineer):**
- Add `staffs` and `config` tables to `db.py`; write migration from `agents`
- Implement Staff CRUD endpoints with cascade resolution logic
- Implement config CRUD endpoints with merge logic
- Update `agent_runner.py` to read config env vars from DB at container start
- Update `messages.py` to parse @mentions, resolve Staff, and invoke the adapter
  with `--model`, `--append-system-prompt`, `--agent`, `--resume` (session ID)
- Remove dead `agents` code paths

**Track 2 — Frontend (engineer):**
- Add `/settings` route: global Staff editor + global config key-value editor
- Update Workspace Detail: replace static agent display with Staff list panel
  (shows local + inherited staffs with badges, create/edit/delete)
- Update TopicChat: parse @mentions for autocomplete hint (v1: no autocomplete,
  but display which Staff handled each agent message)

**Track 3 — Tests (tester):**
- Unit tests for cascade resolution (topic wins over workspace wins over global)
- Unit tests for session ID generation per scope
- Unit tests for @mention parsing (found, not found, no @mention)
- Integration tests for Staff CRUD at each scope level
- Integration test for config merge (workspace over global)
- Migration test: existing workspace with agents row → default Staff created
