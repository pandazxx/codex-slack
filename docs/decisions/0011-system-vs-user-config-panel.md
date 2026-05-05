# 0011 Separate system and user-defined variables in the config panel

- Status: accepted
- Date: 2026-05-05
- Supersedes: none
- Builds on: [0009 Runtime Configuration and Staff System](0009-runtime-configuration-and-staff-system.md), [0010 Workspace-level environment variable overrides](0010-workspace-env-var-overrides.md)

## Context

The global Settings page (`Settings.vue`) exposes a free-form key-value editor backed by `PATCH /api/config`. Any string is a valid key. In practice, users must configure a small set of well-known credential variables (`GH_TOKEN`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`) for agents to function. These are injected by `agent_runner.spawn_agent` with explicit precedence rules (MasterSettings environment overrides DB config, which overrides nothing).

The current UI gives no guidance about which keys are meaningful, does not signal when a known credential is missing, and does not distinguish credentials from arbitrary user-defined env vars. Users must read the source or docs to know what to set.

Issue #124 requests a dedicated "System Variables" section that surfaces these known keys with their current values and inline edit controls, separate from arbitrary user-defined vars.

## Decision Drivers

- **Discoverability.** Users should see exactly which credentials the system expects without reading source code.
- **Storage stays flat.** The `config` table is a generic key-value store with no type column. Adding a `is_system` column would couple storage to a UI taxonomy that will drift.
- **Single source of truth for the list.** If `agent_runner.py` gains a new credential, there must be one canonical place to update; the UI must reflect it without a separate frontend edit.
- **Reuse existing API.** Both system and user-defined variables use the same `PATCH /api/config` endpoint — the distinction is display-only.

## Considered Options

### Where does the canonical system-variable list live?

A. **Frontend constant** (`Settings.vue`): a hardcoded array of `{ name, sensitive }` objects.
B. **Backend endpoint** (`GET /api/config/system-variables`): returns the list from a Python constant colocated with `agent_runner.py`.
C. **Database metadata column** (`config.is_system`): store the classification in SQLite.

### How are system variables rendered?

1. **Inline in the existing table** with a badge distinguishing system from user-defined rows.
2. **Separate sections** in `Settings.vue`: "System Variables" above, "User-defined Variables" below.

### Guard against duplicate entry?

X. **Warn only**: show an inline warning if the user types a system variable name in the user-defined add form, but allow the save.
Y. **Block**: reject the add at the UI level if the key matches a system variable.

## Decision Outcome

**Chosen: B + 2 + X.**

1. **Backend endpoint (Option B).** A `GET /api/config/system-variables` endpoint returns a JSON array of `{ name: str, sensitive: bool }` objects from a Python constant defined alongside `agent_runner.py` in `runtime_config.py`. The frontend fetches this list on mount; it does not hardcode the names. This ensures the canonical list is colocated with the code that actually injects the variables into containers.

2. **Separate sections (Option 2).** `Settings.vue` renders two sections:
   - **System Variables**: one row per entry from the endpoint. Columns: name (read-only label), current value (masked if `sensitive: true`, "— unset —" if absent from DB config), and a Set/Update + Unset button pair.
   - **User-defined Variables**: the existing free-form table, filtered to exclude any key that appears in the system-variable list.
   Both sections call the same `PATCH /api/config` endpoint.

3. **Warn, don't block (Option X).** If the user types a system variable name in the user-defined add form, show an inline warning ("This is a system variable — use the System Variables section above") but still allow the save. Blocking would be paternalistic and could break scripts that pre-populate config via the API without the UI.

### Why this combination

- **Option B over A**: the list of injected credentials lives in `agent_runner.py`. A frontend constant that mirrors it will silently drift when a new provider key is added (e.g. `GEMINI_API_KEY`). A backend endpoint is ~10 lines and ensures the UI reflects reality.
- **Option B over C**: a database metadata column entangles storage with a UI concept. Every migration becomes load-bearing. The list is static and small; a Python constant is the right place.
- **Option 2 over 1**: separate sections with distinct affordances (inline edit vs. free-form add) make the UX unambiguous. A single mixed table with badges requires more cognitive parsing and is harder to scan.
- **Option X over Y**: the `PATCH /api/config` API is public. Blocking at the UI but allowing at the API would create an inconsistency. A warning is sufficient for the expected use case.

### Consequences

- **Good**
  - Adding a new credential to `agent_runner.py` requires updating only one Python constant; the UI reflects it automatically.
  - Users see at a glance which credentials are missing (value column shows "— unset —").
  - User-defined section is uncluttered by well-known credential rows.
  - No schema migration.
- **Bad / accepted tradeoffs**
  - One additional HTTP request on Settings page mount (`GET /api/config/system-variables`). Negligible; response is static and tiny.
  - The `GET /api/config/system-variables` endpoint exposes the system variable names (not values) to any authenticated client. Acceptable — the names are in the source code anyway.
  - Masking uses the `sensitive` flag from the endpoint; the heuristic fallback in `Settings.vue` (`_SENSITIVE` list) is retained for user-defined keys where no explicit flag is available.

### Convention for adding a new system variable in future

1. Add the credential parameter to `agent_runner.spawn_agent`.
2. Add `{ "name": "NEW_VAR", "sensitive": true }` to `_SYSTEM_VARIABLES` in `runtime_config.py`.
3. No frontend change required.

## Implementation Plan

### Backend (`src/master/runtime_config.py`)

Add a constant and a single read-only endpoint:

```python
_SYSTEM_VARIABLES = [
    {"name": "GH_TOKEN",                 "sensitive": True},
    {"name": "ANTHROPIC_API_KEY",        "sensitive": True},
    {"name": "CLAUDE_CODE_OAUTH_TOKEN",  "sensitive": True},
    {"name": "OPENAI_API_KEY",           "sensitive": True},
]

@global_router.get("/system-variables")
def get_system_variables() -> list[dict]:
    return _SYSTEM_VARIABLES
```

### Frontend (`frontend/src/views/Settings.vue`)

- On mount, fetch `/api/config/system-variables` alongside `/api/config`.
- Render "System Variables" section: one row per system var with current value from config (or "— unset —"), a masked display if `sensitive`, and Set/Unset inline controls.
- Filter the existing "Global Config" table to exclude system variable keys.
- Add an inline warning in the free-form add form when the typed key matches a system variable name.

## Out of Scope

- Workspace-scoped system variables — the system variable list is global; workspace overrides are handled by ADR-0010.
- Validation of credential format (e.g. `ghp_` prefix for GitHub tokens). Future enhancement.
- Encryption at rest — deferred per ADR-0009.
