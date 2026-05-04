# 0010 Workspace-level environment variable overrides

- Status: proposed
- Date: 2026-05-04
- Supersedes: none
- Builds on: [0009 Runtime Configuration and Staff System](0009-runtime-configuration-and-staff-system.md)

## Context

ADR-0009 introduced a `config` table with `(scope_type, scope_id, key, value, updated_at)` and shipped the plumbing required to read workspace-scoped env vars at agent container start:

- `runtime_config.load_agent_env(db_path, workspace_id)` merges global + workspace config into a single env dict, with workspace winning on key collisions.
- `agent_runner.spawn_agent(..., extra_env=...)` passes that dict straight to `docker.containers.run(environment=env)`.
- `POST /api/workspaces/{id}/restart-agent` already wires the two together.
- `GET /api/workspaces/{id}/config` and `PATCH /api/workspaces/{id}/config` already exist for reading and writing workspace-scoped config rows.
- `Settings.vue` exposes the same key-value editor for global config.

What is missing is the **workspace-scoped UI surface**: there is no way for a user looking at a single workspace to view, add, or remove env vars that apply only to that workspace, and no defined behaviour for getting those changes into the running container.

This ADR resolves the four open product questions — auto-restart cadence, secret masking, override precedence, and behaviour when the container is stopped — and specifies the UI surface that ties the existing pieces together. No new backend tables or endpoints are introduced.

## Decision Drivers

- **Operator ergonomics.** Adding an env var should be a one-click flow; users should not have to remember to restart the agent afterwards.
- **Predictability.** Users must always be able to tell which value is in effect and where it came from.
- **No silent restarts.** Restarting an agent kills any in-flight run. Users must consent to that, or at minimum be told it just happened.
- **Reuse what exists.** ADR-0009 already settled the data model and merge semantics; this ADR must not re-litigate them.
- **Single-user, self-hosted threat model.** Same posture as ADR-0009 — secrets are stored in plaintext in SQLite; the UI is not the security boundary.

## Considered Options

### Auto-restart cadence

1. **Restart immediately on every individual add/delete.**
2. **Batch edit mode with an explicit "Save & Restart" button.**
3. **Save without restart; show a "restart required" banner and let the user decide.**

### Secret masking

A. **Mask values whose key matches a heuristic (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`) by default; provide a "reveal" toggle.**
B. **Mask everything by default.**
C. **No masking.**

### Override precedence vs. global config

I. **Workspace overrides global** (same as ADR-0009 already specifies for the merge).
II. **Workspace cannot shadow global keys** (workspace rows for a global key are rejected at write time).

### Behaviour when container is not running

X. **Save the row but skip the restart; show a non-blocking notice that the value will apply on next start.**
Y. **Refuse the save until the agent is started.**
Z. **Save the row and start the container.**

## Decision Outcome

**Chosen:** **3 + A + I + X.**

1. **Auto-restart cadence — Option 3 (save-then-prompt).** Each `PATCH` call writes the row immediately. The frontend then shows a sticky "Configuration changed — restart required to apply" banner with a **Restart Agent** button (the same button already on the page). The frontend does **not** call `/restart-agent` automatically.
2. **Secret masking — Option A (heuristic + reveal).** Keys matching a case-insensitive substring list (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `PASSPHRASE`) render their value as `••••••••` with a click-to-reveal eye icon. The list lives in a frontend constant; backend stores and returns plaintext (consistent with ADR-0009 §E).
3. **Override precedence — Option I (workspace wins).** The merge already implemented in `load_agent_env` is the contract. The workspace UI displays inherited global keys read-only with a "global" badge; if the user adds a workspace row with the same key, the workspace value takes effect on next restart and the global row gets a strikethrough "shadowed by workspace" badge.
4. **Container-not-running — Option X (save and defer).** If `get_container_status(workspace_id)` reports anything other than `running`, the save succeeds and the banner reads "Saved. Will apply when the agent next starts." No restart is triggered.

### Why this combination

- **Option 3 over 1**: silent automatic restart on every keystroke is hostile when a long-running agent task is in flight. The user already has a Restart button on the same page; making them press it once after a batch of edits is the right amount of friction.
- **Option 3 over 2**: a batch "Save & Restart" mode requires staging state (pending adds, pending deletes) on the client and a transactional endpoint on the server. The existing `PATCH` endpoint is already row-level. Save-then-prompt gets us 90% of the value with no backend work.
- **Option A over B**: masking everything makes legitimate non-secret values (e.g. `LOG_LEVEL=debug`) needlessly hard to verify. Heuristic masking matches operator intuition.
- **Option I over II**: shadowing global keys at the workspace level is the entire point of having a workspace scope. ADR-0009 already chose this; restating it here only to clarify UI semantics.
- **Option X over Y/Z**: refusing to save without a running container creates a chicken-and-egg problem during initial setup. Auto-starting the container on save is too eager — the user may be in the middle of configuring the workspace and not ready to start the agent.

### Consequences

- **Good**
  - No new backend code paths, tables, or endpoints. Frontend-only feature on top of ADR-0009 plumbing.
  - User always knows when a restart is pending and chooses when to take it.
  - The same Restart Agent button users already understand is the single mechanism for applying any kind of agent-affecting change.
  - Inherited-from-global rows are visible alongside workspace overrides, making the merge transparent.
- **Bad / accepted tradeoffs**
  - User must remember to click Restart; if they navigate away with the banner showing, the change won't apply until they (or someone else) restarts the agent later. Mitigated by making the banner sticky and visually prominent.
  - Heuristic masking will miss vendor-specific keys that don't contain any of the substrings (e.g. `STRIPE_SK_LIVE`). A small risk; users can rename keys or extend the list in a follow-up.
  - Plaintext storage in SQLite remains, per ADR-0009.
  - No CSRF / audit trail for env var changes — same posture as the rest of the v3 API.

### Confirmation

- UI manual UAT in the test plan: add a key, observe banner, click Restart, verify env var lands in the running container via `docker exec ... env`.
- Heuristic masking is unit-tested in the frontend (Vitest) against a fixed list of representative key names.
- Inherited-row badge logic verified with a workspace that shadows a global key.

## Pros and Cons of the Options

### Auto-restart cadence

| Option | Pro | Con |
|---|---|---|
| 1 — Restart on every edit | Zero-cognitive-load: state on disk = state in container | Kills in-flight runs without consent; multiple edits cause multiple restarts; bad UX during initial bulk setup |
| 2 — Batch with Save & Restart | Atomic from the user's POV | Requires client-side staging and either a bulk endpoint or N sequential PATCHes wrapped in optimistic UI; more code, marginal gain |
| 3 — Save now, restart on demand (chosen) | Reuses existing PATCH and Restart Agent button; user controls timing | Pending-restart state must be made obvious or users will forget |

### Secret masking

| Option | Pro | Con |
|---|---|---|
| A — Heuristic + reveal (chosen) | Sensible defaults; non-secret values still readable | Heuristic will miss some vendor-specific secret keys |
| B — Mask everything | Safest default | Verifying non-secret values requires a click for every row; annoying |
| C — No masking | Simplest | Shoulder-surfing risk during screen-shares and demos |

### Override precedence

| Option | Pro | Con |
|---|---|---|
| I — Workspace wins (chosen) | Matches the merge already implemented in `load_agent_env`; matches ADR-0009 | None — already settled |
| II — Reject shadowing | Forces global hygiene | Breaks the entire point of workspace scoping; contradicts ADR-0009 |

### Container-not-running

| Option | Pro | Con |
|---|---|---|
| X — Save and defer (chosen) | Allows full pre-start configuration | User may forget the deferred change exists |
| Y — Refuse save | Forces consistency between stored config and running container | Breaks first-time setup; nothing to save against |
| Z — Auto-start on save | Always-applied semantics | Premature: the user may not be ready to spawn the container yet |

## Implementation Plan

Frontend-only. No backend changes required.

### 1. New component: `WorkspaceEnvVarsPanel.vue`

Lives in the existing workspace detail page, near the Agent Status / Restart Agent area.

Reads from `GET /api/workspaces/{id}/config` (returns merged view per ADR-0009 §6) and renders:

- A table of rows: `key`, `value` (masked if heuristic matches), `source` badge (`workspace` or `global`), and a per-row delete button (disabled for `global`-only rows).
- An "Add" form (key + value text inputs + Add button) styled to match `Settings.vue`.
- A sticky **"Configuration changed — restart required"** banner shown whenever there is at least one pending change since the last successful container start. Includes the existing Restart Agent button (or wires through to it).

### 2. State management

- On mount and after each successful mutation: re-fetch the merged config.
- Track a local `dirtySinceLastStart` flag, persisted to `localStorage` keyed by `workspace_id` so the banner survives page reloads. Cleared on a successful Restart Agent response.
- Distinguish `workspace` rows from inherited `global` rows by cross-referencing `GET /api/workspaces/{id}/config` (merged) against `GET /api/config` (global only). The `inherited_from` field is not yet present on the config response — if missing, infer source by set difference. (Open question below.)

### 3. Add flow

1. User types `KEY` and `VALUE`, clicks Add.
2. `PATCH /api/workspaces/{id}/config` with `{ KEY: VALUE }`.
3. On 200, refetch config, set `dirtySinceLastStart=true`, show banner.
4. If `KEY` already exists at global scope, the new row is shown with a "shadows global" indicator.

### 4. Delete flow

1. User clicks the trash icon on a workspace row.
2. Confirm dialog.
3. `PATCH /api/workspaces/{id}/config` with `{ KEY: null }` (per ADR-0009 §6 PATCH-as-upsert/delete semantics; confirm the actual sentinel during implementation).
4. On 200, refetch, set `dirtySinceLastStart=true`, show banner.

### 5. Masking

Frontend constant:

```ts
const SECRET_KEY_SUBSTRINGS = ['KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'PASSPHRASE'];
```

A row is masked iff `SECRET_KEY_SUBSTRINGS.some(s => key.toUpperCase().includes(s))`. Click-to-reveal toggles a per-row `revealed` flag (component-local; not persisted).

### 6. Container-status awareness

Reuse the agent-status query already on the page. The Restart Agent button is enabled regardless of state (the existing endpoint handles both spawn-fresh and restart cases). The banner copy adapts:

- Container running: "Configuration changed — restart required to apply. **[Restart Agent]**"
- Container not running: "Saved. Will apply when the agent next starts. **[Start Agent]**"

### 7. Test plan

Lives at `docs/test-plans/workspace-env-var-overrides.md`. Cases:

- Add a non-secret key, restart, verify `docker exec` shows it.
- Add a `*_TOKEN` key, verify masking and reveal toggle.
- Add a workspace key that shadows a global key, restart, verify workspace value wins.
- Delete a workspace row that shadows global, restart, verify global value reappears.
- Save while container is stopped, start agent, verify env var present.
- Reload page with pending changes, verify banner persists.

### Open Questions

- [ ] Does `PATCH /api/workspaces/{id}/config` accept `null` as a delete sentinel, or is there a separate `DELETE` shape? Confirm before implementation. (Owner: engineer at slice start.)
- [ ] Does the merged `GET /api/workspaces/{id}/config` already include an `inherited_from` field? If not, decide whether to add it server-side or infer client-side via set difference. (Owner: engineer at slice start.)

## Out of Scope

- Topic-scoped env vars — explicitly rejected in ADR-0009 §D.
- Encryption at rest — deferred per ADR-0009 §E.
- Bulk import/export of env vars (e.g. paste a `.env` file). Future enhancement; not blocking.
- Audit log of who changed what when. Single-user deployment; not warranted.
- Validation of key shape (e.g. POSIX env var name rules). Browser-level minimal check only; server is the source of truth.
