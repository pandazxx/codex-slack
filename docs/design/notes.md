# Design: Workspace and topic notes

**Status:** accepted
**Author:** architect
**Date:** 2026-05-14
**Related ADRs:** [ADR-0015](../decisions/0015-notes-feature.md); builds on ADR-0009 (Staff system) and ADR-0013 (Event-based staff actions).

## Context

Staff system prompts and event-action prompt templates are static strings today. Users want to maintain freeform notes — standing context, project goals, memory, recurring instructions — at workspace or topic scope and have them injected into prompts automatically. The injection must be explicit and scoped, so workspace notes and topic notes do not silently collide.

## Goals

- One `notes` table covering both workspace and topic scope.
- Notes are key/value pairs tagged for filtering; types emerge from tags, not schema.
- Injection via `{{ws:note:keylist:<tag>}}` markers in system prompts and prompt templates.
- Full CRUD API at both scopes.
- Coexist cleanly with existing `{variable}` substitution (`format_map`).
- v1: workspace-scope injection only.

## Non-Goals

- Topic-scope injection (`{{t:note:keylist:<tag>}}`). Deferred to v2 — it changes whether staff system prompts retain their "constant per dispatch" semantic and needs a separate ADR call.
- Versioning or history of note values.
- Note ordering within a tag group (sorted by key).
- Any injection verb other than `keylist` in v1 (`value`, `json`, etc. are future).

## Design

### 1. Data model

New table — pure additive migration (append `CREATE TABLE IF NOT EXISTS` to `_SCHEMA` in `src/master/db.py`):

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

- `id` — UUIDv4 string (consistent with other tables).
- `key` — URL-safe slug, unique per `(scope_type, scope_id)`. Immutable after creation.
- `tags` — JSON array; queried with SQLite's `json_each`. No schema cap on array length.
- No FK on `scope_id` — same pattern as `event_actions`; avoids a hard scope-choice coupling.

### 2. Injection marker syntax

```
{{ws:note:keylist:<tag>}}
```

| Segment | Meaning |
|---|---|
| `ws` | workspace scope (`t` = topic, v2 only) |
| `note` | object type (notes table) |
| `keylist` | render verb — emit matching notes as sorted `key: value` lines |
| `<tag>` | tag string to filter by |

Example: `{{ws:note:keylist:memory}}` injects all workspace notes tagged `memory` as:

```
project_goal: Ship v1 by end of Q2
stack: Python + FastAPI + SQLite + Vue
timezone: Asia/Shanghai
```

Empty tag match → empty string (not the literal marker). Unknown verb or unsupported scope → literal marker + WARNING log.

### 3. Two-pass `render_template`

The existing `render_template` in `src/master/event_actions.py` uses Python's `str.format_map`. Double-brace `{{...}}` is currently Python's escape for a literal brace (`{`). The note markers use `{{...}}` deliberately so they pass through `format_map` untouched without a pre-pass; the regex pre-pass runs *before* `format_map` to intercept them.

```python
_NOTE_MARKER_RE = re.compile(
    r'\{\{(ws|t):note:keylist:([a-z0-9_-]+)\}\}',
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
        scope, tag = m.group(1).lower(), m.group(2)
        if scope == 't':
            LOGGER.warning("note_marker.scope_unsupported_in_v1 marker=%s", m.group(0))
            return m.group(0)
        if conn is None or workspace_id is None:
            LOGGER.warning("note_marker.no_db_context marker=%s", m.group(0))
            return m.group(0)
        rows = conn.execute(
            "SELECT key, value FROM notes"
            " WHERE scope_type='workspace' AND scope_id=?"
            "   AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value=?)"
            " ORDER BY key",
            (workspace_id, tag),
        ).fetchall()
        return "\n".join(f"{r['key']}: {r['value']}" for r in rows)

    text = _NOTE_MARKER_RE.sub(_resolve, template)
    return text.format_map(_SafeDict(variables))
```

Existing callers pass no `conn`/`workspace_id`/`topic_id` and are unaffected — the pre-pass is a no-op when the regex finds no matches.

### 4. System prompt injection

Staff system prompts go through `render_template` in `dispatch.py` before being included in the MQTT payload. Today the system prompt is passed as a raw string (line 131). Change: route it through `render_template(staff["system_prompt"], {}, conn=conn, workspace_id=workspace_id)` at dispatch time. This makes note injection available in system prompts with no extra plumbing.

### 5. API surface

All endpoints under `/api`. No auth — consistent with the rest of the codebase (single-user self-hosted).

*Workspace-scoped notes:*

```
GET    /api/workspaces/{wid}/notes
POST   /api/workspaces/{wid}/notes
GET    /api/workspaces/{wid}/notes/{key}
PATCH  /api/workspaces/{wid}/notes/{key}
DELETE /api/workspaces/{wid}/notes/{key}
```

*Topic-scoped notes:*

```
GET    /api/workspaces/{wid}/topics/{tid}/notes
POST   /api/workspaces/{wid}/topics/{tid}/notes
GET    /api/workspaces/{wid}/topics/{tid}/notes/{key}
PATCH  /api/workspaces/{wid}/topics/{tid}/notes/{key}
DELETE /api/workspaces/{wid}/topics/{tid}/notes/{key}
```

`key` is addressed directly as a path segment (not `id`) because it is human-meaningful and URL-safe.

*Pydantic models:*

```python
class NoteIn(BaseModel):
    key: str        # immutable after creation
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
    # key is read-only; not patchable
```

### 6. Frontend

A *Notes* card on the workspace settings page (alongside the existing staff/config panels) and on the topic settings page (`TopicSettings.vue`, introduced by ADR-0013).

Card layout:

```
┌── Notes ───────────────────────────────────────── [+ Add note] ──┐
│  key         value preview…       tags            [Edit] [✕]     │
│  project_goal  Ship v1 by end…   memory, context  [Edit] [✕]     │
└──────────────────────────────────────────────────────────────────┘
```

Inline edit form: `key` (read-only after first save), `value` (textarea), `tags` (comma-separated input). Help text next to the `key` field documents the injection syntax and available tags.

### 7. Migration

Additive only — one `CREATE TABLE IF NOT EXISTS` and one index appended to `_SCHEMA`. No `ALTER` step. Existing deployments pick up the table on next process start.

## Alternatives Considered

### Extend `config` with `tags`

The `config` table already has `scope_type / scope_id / key / value`. Adding a `tags` column would avoid a new table. Rejected: `config` owns the system-settings and env-var surface (settings panel UX); mixing user notes into it creates ownership confusion. `config` also has no topic scope.

### Single text blob per scope

One freeform text field per workspace/topic. Simplest possible model. Rejected: no granular injection (you can only inject the whole blob), no tagging, no per-note update semantics.

### `{{ws.note.keylist.memory}}` (dot syntax)

Using dots instead of colons in the marker. Rejected: conflicts with Python's `format_map` attribute-access syntax; colons are neutral inside `{{...}}`.

## Open Questions

### Resolved

- [x] *Scope*: both workspace and topic in v1 CRUD; workspace injection only in v1 renderer. See §3.
- [x] *Injection syntax*: namespaced `{{ws:note:keylist:<tag>}}`. See §2.
- [x] *Conflict resolution*: explicit scope in marker — no implicit shadowing. See §2.
- [x] *New table vs. extending `config`*: new `notes` table. See Alternatives Considered.
- [x] *`key` mutability*: immutable post-create; renames are delete-then-recreate.
- [x] *`keylist` output format*: sorted `key: value` newline list; empty match → empty string.

### Deferred

- *Topic-scope injection (`{{t:note:keylist:<tag>}}`)*: changes whether system prompts are "constant per dispatch." Requires a v2 ADR decision on that semantic before implementing.
- *Additional render verbs* (`{{ws:note:value:key}}` for a single note value). Not needed in v1.
- *Cross-scope inheritance* (topic notes falling back to workspace notes for the same key). Not in scope.

## Test plan key cases

- CRUD round-trip for workspace-scoped notes; same for topic-scoped.
- `UNIQUE (scope_type, scope_id, key)` constraint: duplicate key on POST returns 409.
- `key` is not patchable: PATCH with `key` field returns 422.
- Tag filtering: note with `tags=["memory","context"]` is returned by `keylist:memory` and `keylist:context` but not `keylist:goal`.
- `keylist` output is sorted by `key`; two notes produce two lines.
- Empty tag match → empty string substitution (not the literal marker).
- `{{t:note:keylist:memory}}` in v1 → literal marker preserved + WARNING log emitted.
- Marker in staff `system_prompt` is resolved at dispatch time via `render_template`.
- Marker in event-action `prompt_template` is resolved by the event worker.
- Existing `{variable}` placeholders in templates coexist with note markers (two-pass does not corrupt them).
- `render_template` called without `conn` / `workspace_id`: note markers left literal + WARNING; existing `{variable}` substitution unaffected.
