# Test Plan: Workspace-level Environment Variable Overrides

- **Feature design:** [docs/decisions/0010-workspace-env-var-overrides.md](../decisions/0010-workspace-env-var-overrides.md)
- **Date:** 2026-05-04
- **Components under test:** `WorkspaceEnvVarsPanel.vue`, `WorkspaceDetail.vue` (integration)

---

## 1. Scope

### In scope

- `WorkspaceEnvVarsPanel` component rendered on the Workspace Detail page.
- Add and delete workspace-scoped env var rows via `PATCH /api/workspaces/{id}/config`.
- Secret masking heuristic and click-to-reveal toggle.
- Source badge logic: `workspace`, `workspace (shadows global)`, `global`.
- Sticky "restart required" banner and its localStorage persistence across page reloads.
- Banner copy adapting to container running vs. stopped state.
- Banner cleared on a successful Restart Agent call.
- Inherited (`global`) rows being read-only.
- End-to-end propagation: env var saved in UI lands in the agent container environment after restart.

### Out of scope

- Backend API correctness and data persistence (tested separately via API-level tests).
- Topic-scoped env vars (explicitly out of scope per ADR-0010).
- Encryption at rest (deferred per ADR-0009).
- Bulk import/export of env vars.
- POSIX name validation beyond the empty-key guard.
- Audit logging of env var changes.
- Multi-user or access-control scenarios (single-user self-hosted deployment).

---

## 2. Test Environment Prerequisites

- A running deployment reachable at the UI URL (local `docker compose up` is sufficient).
- At least one workspace exists with an agent container that can be started and stopped.
- Docker (or Podman) CLI accessible on the host so `docker exec <container> env` can be run during container-level verification steps.
- Browser developer tools available for localStorage inspection.
- One or more global env vars pre-configured via the Settings page (needed for shadow tests).

---

## 3. Test Cases

### TC-01: Add a non-secret env var, restart, verify in container

**Precondition:** Workspace exists. Agent container is running. No existing workspace var named `LOG_LEVEL`.

**Steps:**
1. Navigate to the Workspace Detail page.
2. In the Env Vars panel, enter `LOG_LEVEL` in the Key field and `verbose` in the Value field.
3. Click **Add / Update**.
4. Observe the table and the page banner.
5. Click **Restart Agent** and confirm the dialog.
6. Wait for the agent status badge to return to `Running`.
7. On the host, run: `docker exec <agent-container-name> env | grep LOG_LEVEL`

**Expected result:**
- Step 3: the row `LOG_LEVEL = verbose` appears in the table with a `workspace` badge.
- Step 4: a sticky orange banner reading "Configuration changed — restart required to apply." appears above the table. The Restart Agent button is visible in the status bar.
- Step 5–6: no error; agent status returns to Running.
- Step 6: the banner disappears.
- Step 7: output includes `LOG_LEVEL=verbose`.

**Pass criteria:** All of the above hold. Fail if the var is absent from `docker exec env`, if the banner does not appear, or if the banner does not clear after a successful restart.

---

### TC-02: Add a secret-heuristic key, verify masking and reveal toggle

**Precondition:** Workspace exists. No existing workspace var named `MY_TOKEN`.

**Steps:**
1. Navigate to the Workspace Detail page.
2. Enter `MY_TOKEN` in the Key field and `s3cr3t-value` in the Value field. Click **Add / Update**.
3. Locate the `MY_TOKEN` row in the table.
4. Observe the value cell without interaction.
5. Click the **reveal** link next to the masked value.
6. Observe the value cell after reveal.
7. Click the **hide** link.
8. Observe the value cell after hiding.

**Expected result:**
- Step 4: the value cell shows `••••••••` (masked). The plaintext `s3cr3t-value` is not visible in the DOM text content.
- Step 5–6: the value cell shows `s3cr3t-value` in plaintext. A **hide** link appears.
- Step 7–8: the value reverts to `••••••••`.

**Pass criteria:** Masking is active by default for a key containing `TOKEN`. Reveal and hide cycle works correctly. The plaintext value is never rendered as visible text when the row is masked.

Additional key-name assertions (can be verified in the same session or as sub-steps):
- Keys containing `KEY`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `PASSPHRASE` (case-insensitive) are masked by default.
- Keys not matching any substring (e.g. `LOG_LEVEL`, `TIMEOUT_SECONDS`) are shown in plaintext without a reveal button.

---

### TC-03: Add a workspace key that shadows a global key, verify badge and correct precedence after restart

**Precondition:** A global env var `GLOBAL_VAR=global-value` exists (set via Settings page). No workspace override for `GLOBAL_VAR` exists on this workspace.

**Steps:**
1. Navigate to the Workspace Detail page.
2. Locate the `GLOBAL_VAR` row in the Env Vars table. Confirm it shows the `global` badge and the delete button is absent (or disabled) for that row.
3. In the Add form, enter `GLOBAL_VAR` as the key and `workspace-value` as the value. Click **Add / Update**.
4. Observe the `GLOBAL_VAR` row in the table.
5. Click **Restart Agent** and confirm.
6. Wait for the agent status badge to return to `Running`.
7. On the host, run: `docker exec <agent-container-name> env | grep GLOBAL_VAR`

**Expected result:**
- Step 2: `GLOBAL_VAR` is shown with a `global` badge (purple). No delete button.
- Step 4: the row for `GLOBAL_VAR` now shows the value `workspace-value` with a `workspace (shadows global)` badge (amber/yellow). A delete button is present for this row.
- Step 6: banner clears.
- Step 7: output includes `GLOBAL_VAR=workspace-value` (workspace value wins).

**Pass criteria:** Badge changes from `global` to `workspace (shadows global)` after adding the override. Container shows workspace value, not global value.

---

### TC-04: Delete a shadowing workspace row, verify global value reappears after restart

**Precondition:** TC-03 completed. `GLOBAL_VAR` has a workspace override with value `workspace-value`. Agent is running.

**Steps:**
1. On the Workspace Detail page, locate the `GLOBAL_VAR` row showing the `workspace (shadows global)` badge.
2. Click the **✕** (delete) button for that row.
3. Confirm the deletion dialog.
4. Observe the table.
5. Click **Restart Agent** and confirm.
6. Wait for the agent status badge to return to `Running`.
7. On the host, run: `docker exec <agent-container-name> env | grep GLOBAL_VAR`

**Expected result:**
- Step 4: the `GLOBAL_VAR` row reverts to showing the `global` badge and the original global value. The delete button disappears for that row.
- Step 5–6: restart succeeds; banner clears.
- Step 7: output includes `GLOBAL_VAR=global-value` (the global value, not the deleted workspace override).

**Pass criteria:** After deleting the workspace override, the global value takes precedence in the container.

---

### TC-05: Add a key while container is stopped — banner says "will apply on next start", then start agent

**Precondition:** Workspace exists. Agent container is stopped (status shows `Stopped` or `Not found`). No workspace var named `STARTUP_VAR`.

**Steps:**
1. Navigate to the Workspace Detail page. Confirm the agent status badge shows a non-running state.
2. Enter `STARTUP_VAR` in the Key field and `hello` in the Value field. Click **Add / Update**.
3. Observe the banner.
4. Click **Restart Agent** (or **Start Agent** if the button label adapts) and confirm.
5. Wait for the agent status badge to show `Running`.
6. On the host, run: `docker exec <agent-container-name> env | grep STARTUP_VAR`

**Expected result:**
- Step 3: the banner reads "Saved. Will apply when the agent next starts." (not "restart required to apply").
- Step 5: agent transitions to Running.
- Step 5: banner clears.
- Step 6: output includes `STARTUP_VAR=hello`.

**Pass criteria:** Banner copy correctly reflects the stopped-container variant. Env var lands in the container after the agent is started.

---

### TC-06: Reload page with pending banner — banner persists via localStorage

**Precondition:** At least one workspace env var has been added since the last agent restart (i.e. the restart-required banner is currently showing).

**Steps:**
1. Confirm the sticky banner is visible on the Workspace Detail page.
2. Open browser developer tools → Application → Local Storage. Confirm a key matching `workspace_<id>_dirty` is set to `"true"`.
3. Reload the page (hard reload, Ctrl+Shift+R).
4. Observe the banner after reload.

**Expected result:**
- Step 2: localStorage entry is present with value `"true"`.
- Step 4: the banner is shown immediately after reload without requiring any user interaction. The table rows (and the dirty state) are intact.

**Pass criteria:** Banner reappears after a full page reload. Fail if the banner is absent after reload while the localStorage key is still set.

---

### TC-07: Attempt to add a row with an empty key — rejected

**Precondition:** Workspace detail page is open.

**Steps:**
1. Leave the Key field empty. Enter any value in the Value field.
2. Observe the **Add / Update** button state.
3. Attempt to submit by pressing Enter in the Value field.
4. Observe whether a new row is added or an error appears.

**Expected result:**
- Step 2: the **Add / Update** button is disabled (the `:disabled="!newKey.trim() || saving"` binding prevents submission).
- Step 3: pressing Enter does not submit (the `@keydown.enter="addVar"` handler calls `addVar`, which returns immediately when `!key`).
- Step 4: no new row appears; no error message is shown; no API call is made.

**Pass criteria:** Empty-key submission is silently blocked at the UI level. No API request is fired. Fail if a row with an empty or whitespace-only key appears in the table, or if a network request is logged.

---

### TC-08: Successful restart clears the banner

**Precondition:** The restart-required banner is currently shown (at least one pending workspace var change). Agent is running.

**Steps:**
1. Confirm the banner is visible.
2. Click **Restart Agent** in the agent status bar. Confirm the dialog.
3. Observe the banner while the restart is in progress (button shows "Restarting…").
4. Wait for the agent status badge to return to `Running`.
5. Observe the banner after the restart completes.

**Expected result:**
- Steps 2–3: banner remains visible during the restart operation.
- Step 5: the banner is no longer visible.
- Verify in browser dev tools → Local Storage: the `workspace_<id>_dirty` key has been removed.

**Pass criteria:** Banner disappears exactly once, on a successful restart response (`POST /api/workspaces/{id}/restart-agent` returns 2xx). Fail if the banner remains after a successful restart, or if the localStorage key is not removed.

---

## 4. Non-functional Test Cases

### NF-01: Secret masking is complete — value not exposed in DOM

**Steps:**
1. Add a key matching the secret heuristic (e.g. `API_SECRET`).
2. With the value masked (reveal not clicked), open browser developer tools → Elements.
3. Search the DOM for the plaintext value string.

**Expected result:** The plaintext value does not appear anywhere in the rendered DOM while the row is masked. Only `••••••••` appears in the value cell.

**Pass criteria:** Zero occurrences of the plaintext secret in the DOM when masked. Fail if the value is present in a hidden element, `data-*` attribute, or inline style.

---

### NF-02: Inherited (global) rows are read-only

**Steps:**
1. Ensure at least one global env var is inherited and visible in the table with a `global` badge.
2. Confirm the row has no delete button rendered.
3. Confirm no inline edit affordance is present for that row.
4. Confirm the "manage in Settings" link is shown instead.

**Expected result:** Global rows have no interactive delete control. The link to Settings is present. Attempting to call the PATCH endpoint manually with a `delete` payload for a key that only exists at global scope leaves that key present in the merged config on reload (the backend is the authority; this is a UI read-only assertion).

**Pass criteria:** No delete button rendered for `source === 'global'` rows; "manage in Settings" link present.

---

### NF-03: No XSS from key or value inputs

**Steps:**
1. In the Key field, enter: `<img src=x onerror=alert(1)>`
2. In the Value field, enter: `"><script>alert(1)</script>`
3. Click **Add / Update**.
4. Observe the table row that appears.

**Expected result:** The key and value are rendered as escaped text in the table cells. No alert dialog fires. No raw HTML is injected into the DOM.

**Pass criteria:** Vue's default template binding (`{{ }}`) escapes the strings. No script execution occurs. Fail if an `alert` fires or if raw HTML tags appear as rendered elements.

---

## 5. Out of Scope

The following items are explicitly not covered by this test plan:

- Backend persistence correctness (`PATCH /api/workspaces/{id}/config` API contract, SQLite row storage, merge logic in `runtime_config.load_agent_env`). These are covered by server-side unit and integration tests.
- Topic-scoped env vars — rejected in ADR-0009 §D, not implemented.
- Encryption at rest — deferred per ADR-0009 §E.
- Bulk `.env` file import/export.
- Audit log of changes.
- Multi-user or RBAC scenarios.
- Automated unit tests for the `SECRET_KEY_SUBSTRINGS` constant in isolation (these would live in Vitest component tests, not in this manual plan).
- Archived workspaces — the `WorkspaceEnvVarsPanel` is not rendered for archived workspaces (`v-if="!isArchived"`), so env var interactions on archived workspaces require no test coverage here.

---

## 6. Sign-Off Template

| Field | Value |
|---|---|
| Date | |
| Build / commit under test | |
| Browser(s) tested | |
| Docker/Podman CLI version | |
| Test executor | |
| TC-01 result | Pass / Fail |
| TC-02 result | Pass / Fail |
| TC-03 result | Pass / Fail |
| TC-04 result | Pass / Fail |
| TC-05 result | Pass / Fail |
| TC-06 result | Pass / Fail |
| TC-07 result | Pass / Fail |
| TC-08 result | Pass / Fail |
| NF-01 result | Pass / Fail |
| NF-02 result | Pass / Fail |
| NF-03 result | Pass / Fail |
| Blocking issues | |
| Sign-off owner | |
