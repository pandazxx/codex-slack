---
title: "ADR-0015: Workspace and topic notes with prompt injection"
status: accepted
date: 2026-05-14
decision-makers: [engineer, architect]
consulted: [user]
informed: [doc-writer]
---

## Context and Problem Statement

Users want to maintain freeform notes scoped to a workspace or topic — things like standing context, project goals, recurring instructions, or memory — and have those notes automatically injected into staff system prompts or event-action prompt templates without manual copy-paste. No existing mechanism covers this: the `config` table stores system and env-var settings only, and system prompts are static strings today.

## Decision Drivers

- Notes must be queryable by tag (not typed by schema) so a single model covers "memory", "context", "goals", etc.
- Injection must coexist cleanly with the existing `{variable}` substitution in `render_template` (Python `format_map`).
- The injection scope must be explicit in the marker so workspace- and topic-scoped notes never silently collide.
- v1 scope: workspace injection only; topic injection is deferred to v2.

## Considered Options

1. Extend the `config` table with a `tags` column.
2. Dedicated `notes` table with key / value / tags.
3. Unstructured text blob per scope (no key/value).

## Decision Outcome

*Chosen option:* Option 2 — dedicated `notes` table — because it keeps notes isolated from system config, allows per-note tagging, and the key-value shape gives injection a stable addressing unit.

### Consequences

- *Good:* tags replace types — note categories are user-defined and composable, not schema-constrained.
- *Good:* injection is fully explicit (`{ws:note:keylist:memory}`) — no implicit shadowing between scopes.
- *Good:* additive schema change; existing deployments pick it up on next process start.
- *Bad:* `key` is immutable post-create; renames require delete-then-recreate.
- *Bad:* topic-scope injection (`{t:note:keylist:<tag>}`) is not supported in v1; markers are left literal with a WARNING log.

### Confirmation

CRUD round-trip tests per scope; injection tests for known/unknown tags, empty matches, and `{t:...}` fallback. CI gate enforces green before merge.

## Pros and Cons of the Options

### Option 1: Extend `config` with `tags`

Add `tags TEXT` column to the existing `config` table.

- Pro: no new table, migration is a single `ALTER TABLE`
- Con: mixes system/env config with user-facing notes; `config` is already owned by a different UX surface (settings panel)
- Con: `config` key uniqueness is scoped globally/workspace only — no topic scope

### Option 2: Dedicated `notes` table

New table with `(scope_type, scope_id, key)` UNIQUE, `value TEXT`, `tags TEXT` (JSON array).

- Pro: clean separation from system config
- Pro: full workspace and topic scope support
- Pro: `tags` column enables flexible multi-tag filtering with no schema changes
- Con: one more table

### Option 3: Unstructured blob per scope

Single text note per scope; no key/value split.

- Pro: simplest possible
- Con: no granular injection — you can only inject the whole blob
- Con: no tagging; every injection includes everything

## Implementation Notes

**Schema — new `notes` table (add to `_SCHEMA` in `src/master/db.py`):**

```sql
CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    scope_type  TEXT NOT NULL CHECK (scope_type IN ('workspace', 'topic')),
    scope_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '[]',  -- JSON array of strings
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (scope_type, scope_id, key)
);
CREATE INDEX IF NOT EXISTS idx_notes_scope ON notes (scope_type, scope_id);
```

No FK on `scope_id` — same pattern as `event_actions`.

**Injection marker syntax:**

```
{ws:note:keylist:<tag>}
```

- `ws` — workspace scope (v1 only; `t` = topic scope, deferred to v2)
- `note` — object type
- `keylist` — render verb: emits all matching notes as a sorted `key: value\n` list
- `<tag>` — tag to filter by; notes must contain this tag

Empty tag match → empty string substitution (not the literal marker).

**Marker resolution — single-pass `render_template`:**

A single regex matches both note markers and plain variable placeholders. `str.format_map` is replaced entirely — this avoids two-pass complexity and the colon-as-format-spec-separator problem:

```python
import re

_TEMPLATE_RE = re.compile(
    r'\{(ws|t):note:keylist:([a-z0-9_-]+)\}'   # note marker
    r'|\{([a-zA-Z_][a-zA-Z0-9_]*)\}',           # plain variable
    re.IGNORECASE,
)

def render_template(
    template: str,
    variables: dict[str, str],
    *,
    conn=None,
    workspace_id: str | None = None,
    topic_id: str | None = None,
) -> str:
    def _resolve(m: re.Match) -> str:
        if m.group(1) is not None:  # note marker
            scope, tag = m.group(1).lower(), m.group(2)
            if scope == 't':
                LOGGER.warning("note_marker.scope_unsupported_in_v1 marker=%s", m.group(0))
                return ''
            if conn is None or workspace_id is None:
                LOGGER.warning("note_marker.no_db_context marker=%s", m.group(0))
                return ''
            rows = conn.execute(
                "SELECT key, value FROM notes"
                " WHERE scope_type='workspace' AND scope_id=?"
                "   AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value=?)"
                " ORDER BY key",
                (workspace_id, tag),
            ).fetchall()
            return "\n".join(f"{r['key']}: {r['value']}" for r in rows)
        else:  # plain variable
            key = m.group(3)
            if key not in variables:
                LOGGER.warning("render_template.unknown_variable key=%s", key)
                return m.group(0)
            return variables[key]

    return _TEMPLATE_RE.sub(_resolve, template)
```

`_SafeDict` is no longer needed. Callers that do not pass `conn` / `workspace_id` get empty-string substitution for note markers with a WARNING; plain variable handling is unchanged.

**Integration points:**

- `event_actions.py:render_template` — extend with optional `conn`/`workspace_id`/`topic_id` kwargs; wire them through in `_dispatch_one`.
- `dispatch.py` — route `staff["system_prompt"]` through `render_template` before publishing to MQTT, passing `conn` and `workspace_id`. This ensures both event-action templates and staff system prompts pick up note injection from one change.

**API:**

```
GET    /api/workspaces/{wid}/notes
POST   /api/workspaces/{wid}/notes
GET    /api/workspaces/{wid}/notes/{key}
PATCH  /api/workspaces/{wid}/notes/{key}
DELETE /api/workspaces/{wid}/notes/{key}

GET    /api/workspaces/{wid}/topics/{tid}/notes
POST   /api/workspaces/{wid}/topics/{tid}/notes
GET    /api/workspaces/{wid}/topics/{tid}/notes/{key}
PATCH  /api/workspaces/{wid}/topics/{tid}/notes/{key}
DELETE /api/workspaces/{wid}/topics/{tid}/notes/{key}
```

`key` is the slug (URL path segment). `key` is immutable after creation — PATCH cannot change it. Renames are delete-then-recreate.

**Pydantic models:**

```python
class NoteIn(BaseModel):
    key: str          # slug; immutable after creation
    value: str
    tags: list[str] = []

class NoteOut(NoteIn):
    scope_type: str
    scope_id: str
    created_at: str
    updated_at: str

class NotePatch(BaseModel):
    value: str | None = None
    tags: list[str] | None = None
```
