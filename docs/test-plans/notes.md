# Test Plan: Notes Feature

**Design doc:** docs/design/notes.md  
**Branch:** topic/topic-or-workspace-level-note-7c4f67e  
**Date:** 2026-05-14

---

## Scope

Key/value notes with tags, stored at workspace or topic scope. Notes are
injected into staff system prompts and event-action prompt templates via
`{ws:note:notes:<tag>}` markers resolved by `render_template`.

---

## Test Cases

### 1. CRUD round-trip — workspace-scoped notes (`automated`)

POST → 201, body has correct fields; GET by key → 200; GET list; PATCH value → updated; PATCH tags → updated; DELETE → 204, subsequent GET → 404.

### 2. CRUD round-trip — topic-scoped notes (`automated`)

Same sequence as #1 against `/api/workspaces/{wid}/topics/{tid}/notes`.

### 3. Duplicate key on POST → 409 (`automated`)

Posting the same key twice under the same scope returns 409. Posting the same key under a different workspace must succeed (scoping boundary check).

### 4. GET/PATCH/DELETE non-existent key → 404 (`automated`)

All three operations on a key that was never created return 404, for both workspace-scope and topic-scope endpoints.

### 5. PATCH with `key` field present → 422 (`automated`)

`NotePatch` declares `extra="forbid"`; sending `{"key": "new-key", "value": "v"}` in the body must produce 422 Unprocessable Entity.

### 6. Tag filtering in render_template (`automated`)

A note tagged `["memory","context"]` appears in `{ws:note:notes:memory}` and `{ws:note:notes:context}` but NOT in `{ws:note:notes:goal}`.

### 7. notes output sorted by key (`automated`)

Two notes tagged the same tag appear in the substituted string in ascending key order, one `key: value` line each.

### 8. Empty tag match → empty string (`automated`)

A `{ws:note:notes:<tag>}` marker for a tag with no matching notes produces an empty string. The raw marker must not appear in the output.

### 9. `{t:note:notes:…}` → empty string + WARNING (`automated`)

v1 only supports `ws` scope. Topic-scoped markers must produce empty string and emit a `WARNING` log entry containing `scope_unsupported_in_v1`.

### 10. `{variable}` and note markers coexist in one pass (`automated`)

A template with both `{name}` and `{ws:note:notes:tag}` resolves both correctly in a single `render_template` call.

### 11. render_template without db_path/workspace_id (`automated`)

When `db_path` or `workspace_id` is `None`, note markers produce an empty string and emit a WARNING. Plain `{variable}` substitution still works.

### 12. Unknown `{variable}` left literal + WARNING (`automated`)

An unrecognised variable placeholder is left unchanged in the output and produces a `render_template.unknown_variable` WARNING.

### 13. Staff system_prompt injection via MQTT dispatch (`automated`)

A staff whose `system_prompt` contains `{ws:note:notes:memory}` has the note values injected into the MQTT publish payload before delivery.

### 14. Event-action prompt_template injection (`needs-human`)

Create an event action whose `prompt_template` contains `{ws:note:notes:memory}`, send a message that fires it, and confirm the rendered prompt delivered to the agent contains the note value. Requires a running stack with MQTT broker and agent container.

---

## Pass/Fail Criteria

- All `automated` cases: `pytest tests/master/test_notes.py` exits 0, all tests pass.
- Case 14 (`needs-human`): human operator observes the rendered prompt in the running-stack log or agent output.

---

## Non-functional Requirements

- `render_template` must complete its DB query in a single round-trip (no N+1 per tag).
- No existing tests in `tests/master/` may be broken by the notes feature.
