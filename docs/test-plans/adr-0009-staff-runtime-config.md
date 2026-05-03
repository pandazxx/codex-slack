# Test Plan — ADR 0009: Staff System & Runtime Config

**Design doc:** `docs/decisions/0009-runtime-configuration-and-staff-system.md`
**Branch:** `feat/runtime-config-staff-ui`
**PRs:** #97 (backend), #98 (frontend + fixes)

---

## Scope

- Global and workspace staff CRUD
- Staff cascade resolution (topic → workspace → global)
- Runtime config (global and workspace, merge view)
- @mention routing and default-staff fallback
- Session scope (topic / workspace / global)
- Agent invocation — model, system\_prompt, sub-agent flags
- Frontend: Settings page, WorkspaceDetail staffs panel, TopicChat labels

## Out of scope

- Global session scope cross-workspace sharing (single agent container per workspace; true global scope is a future item)
- `extra_flags` field (reserved for future CLI flags)

---

## Automated cases (API + agent layer)

Run via `pytest` for unit/integration tests. The testbed script below covers end-to-end behaviour.

### UAT-01 — Global Staff CRUD

| # | Action | Expected |
|---|--------|----------|
| 01-a | `POST /api/staffs` with valid body | 201, `scope_type=global`, `scope_id=null` |
| 01-b | `GET /api/staffs` | New staff appears in list |
| 01-c | `PUT /api/staffs/{name}` with updated model | 200, model field updated |
| 01-d | `DELETE /api/staffs/{name}` | 204, staff absent from subsequent GET |

### UAT-02 — Global Config CRUD

| # | Action | Expected |
|---|--------|----------|
| 02-a | `PATCH /api/config` `{"set":{"K":"v"}}` | Key present in GET |
| 02-b | `PATCH /api/config` with same key, new value | Upserts (no duplicate) |
| 02-c | `PATCH /api/config` `{"delete":["K"]}` | Key absent from GET |

### UAT-03 — Workspace Staff CRUD + inherited\_from badge

| # | Action | Expected |
|---|--------|----------|
| 03-a | `GET /api/workspaces/{id}/staffs` | Local staffs have `inherited_from=null` |
| 03-b | Global staff visible in workspace list | `inherited_from="global"` |
| 03-c | `POST /api/workspaces/{id}/staffs` | 201, workspace-scoped record created |
| 03-d | `PUT /api/workspaces/{id}/staffs/{name}` | Model and session\_scope updated |
| 03-e | `DELETE /api/workspaces/{id}/staffs/{name}` | 204 |

### UAT-04 — Workspace Config merge

| # | Action | Expected |
|---|--------|----------|
| 04-a | Set global-only key, GET workspace config | Key present with global value |
| 04-b | Set workspace-only key, GET workspace config | Key present |
| 04-c | Set same key in both global and workspace | Workspace value wins in merged GET |

### UAT-05 — Staff cascade (topic → workspace → global)

| # | Action | Expected |
|---|--------|----------|
| 05-a | `POST /api/workspaces/{id}/topics/{id}/staffs` with same name as global staff | 201, `scope_type=topic` |
| 05-b | Topic staff in topic list | `scope_type=topic`, `inherited_from=null` |
| 05-c | Topic staff model differs from global | Topic model returned (global not leaked) |
| 05-d | Delete topic staff, re-list | Global staff reappears with `inherited_from="global"` |

### UAT-06 — @mention routing

| # | Message text | Expected |
|---|-------------|----------|
| 06-a | `@reviewer please review` | 202; MQTT payload `agent_name=reviewer` |
| 06-b | `@nonexistent do something` | 404 |
| 06-c | `plain message` (no mention) | 202; routes to `is_default=true` staff |
| 06-d | `@workspace-scoped-staff hello` | 202 |

### UAT-07 — No default staff → 422

| # | Setup | Action | Expected |
|---|-------|--------|----------|
| 07-a | Set all workspace staffs `is_default=false` | Send message without @mention | 422 |
| 07-b | Same | Check response body | `detail` contains "default staff" |

### UAT-08 — Workspace session scope: single session across topics

| # | Action | Expected |
|---|--------|----------|
| 08-a | Send `@ws-staff` to topic-1 and topic-2; inspect `staff_sessions` table | Exactly one row with `scope_type=workspace` for that staff; same `session_id` for both sends |

### UAT-09 — Model override applied

| # | Setup | Action | Expected |
|---|-------|--------|----------|
| 09-a | Staff has `model=haiku` | Send message, wait for agent reply | Transcript `system.init.model` = `claude-haiku-4-5-20251001` |

### UAT-10 — Duplicate staff name → 409

| # | Action | Expected |
|---|--------|----------|
| 10-a | POST global staff, POST same name again | 409 |
| 10-b | POST workspace staff, POST same name in same workspace | 409 |

### UAT-11 — Archived workspace rejects messages

| # | Setup | Action | Expected |
|---|-------|--------|----------|
| 11-a | Archive workspace via DELETE | POST message to any topic in that workspace | 404 |

### UAT-12 — Topic session scope isolates sessions

| # | Action | Expected |
|---|--------|----------|
| 12-a | Send message from `claude` staff to topic-1 and topic-2 | `staff_sessions` has two rows with `scope_type=topic` and **different** `session_id` values |

### UAT-13 — Workspace session scope uses shared CWD

| # | Action | Expected |
|---|--------|----------|
| 13-a | Send `@ws-scope-staff` to any topic | `/workspace/sessions/{workspace_id}` directory exists in agent container |

---

## Manual (browser) cases

These require a running UI and cannot be scripted.

| # | Steps | Expected |
|---|-------|----------|
| M-01 | Navigate to `/settings` | Page loads with "Global Staff" and "Global Config" sections |
| M-02 | Click "+ Add Staff", fill form, click Save | Staff appears in table without page reload |
| M-03 | Click Edit on existing staff | Form pre-fills; changes persist after save |
| M-04 | Add config key containing `TOKEN` or `API_KEY` | Value shows as `••••••••` in the table |
| M-05 | Open a workspace with a global staff | Global staff row shows purple "global" badge; no Edit/Delete buttons; "manage in Settings" link present |
| M-06 | Configure workspace with no `is_default` staff; send message | Red error text appears below send button; page does not freeze |
| M-07 | Send a message; wait for agent reply | Agent bubble label shows `@staff_name`, not generic "Agent" |
| M-08 | Create workspace-scope staff; send `@staff` in topic-1 ("remember the word banana"); send `@staff` in topic-2 ("what word did I mention?") | Second reply references "banana" (shared session context) |

---

## Testbed execution log

All automated cases executed on testbed `10.10.10.123` on 2026-05-03 against branch `feat/runtime-config-staff-ui` (image `sha256:557e1fbe`). All 24 automated cases passed. All 8 manual cases verified by user.

### Known ops note

`_respawn_agents` skips containers already running. After a master image rebuild, manually remove the agent container before restarting master so the new image is picked up:

```bash
docker rm -f codex-agent-{workspace_id}
docker compose up -d --force-recreate master
```
