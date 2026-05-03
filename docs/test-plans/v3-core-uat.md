# Test Plan — v3 Core System UAT

Covers the baseline capabilities of the v3 stack: web UI, HTTP API, Docker agent containers, MQTT message routing, and SQLite persistence. Feature-specific test plans live alongside this file.

**Stack:** FastAPI master · Vue 3 frontend · Docker agent containers · MQTT (Mosquitto) · SQLite

---

## Test environment

- Testbed: `http://<host>:8080`
- Docker Compose stack running: `master`, `mosquitto`
- At least one workspace with an agent container running

---

## UAT-01 — Workspace lifecycle

| # | Action | Expected |
|---|--------|----------|
| 01-a | `POST /api/workspaces` with name + repo\_url | 201; workspace appears in list |
| 01-b | `GET /api/workspaces` | Returns all active workspaces |
| 01-c | `GET /api/workspaces/{id}` | Returns workspace with `staffs` list |
| 01-d | `DELETE /api/workspaces/{id}` | 204; workspace has `archived_at` set |
| 01-e | Archived workspace absent from active list | `GET /api/workspaces` does not include it |
| 01-f | `GET /api/workspaces/{id}` on archived | Still returns record (read-only) |
| 01-g | Create workspace with a previously-used name (after archive) | 201; reuse allowed |

## UAT-02 — Topic lifecycle

| # | Action | Expected |
|---|--------|----------|
| 02-a | `POST /api/workspaces/{id}/topics` | 201; topic in list |
| 02-b | `GET /api/workspaces/{id}/topics` | Returns active topics only |
| 02-c | `DELETE /api/workspaces/{id}/topics/{id}` | 204; topic has `archived_at` set |
| 02-d | Messages cannot be sent to archived topic | 404 |
| 02-e | Archived topics visible at `/archived-topics` route | UI renders archived list |

## UAT-03 — Agent container lifecycle

| # | Action | Expected |
|---|--------|----------|
| 03-a | Create workspace | Agent container `codex-agent-{id}` starts automatically |
| 03-b | `GET /api/workspaces/{id}/agent-status` | Returns `status=running` |
| 03-c | Master container restart | Agent containers respawn for workspaces whose container is missing |
| 03-d | Archive workspace | Agent container is removed |

## UAT-04 — Message send and agent response

| # | Action | Expected |
|---|--------|----------|
| 04-a | `POST .../messages` form with `text` | 202; `message_id` returned |
| 04-b | User message saved | Appears in `GET .../messages` with `sender=user` |
| 04-c | Agent processes prompt | Agent message appears (via WebSocket or on reload) with `sender=agent` |
| 04-d | Message to archived workspace | 404 |
| 04-e | Message to unknown topic | 404 |

## UAT-05 — WebSocket real-time updates

| # | Action | Expected |
|---|--------|----------|
| 05-a | Open topic in browser, send message | Agent reply appears without page reload |
| 05-b | Agent busy | Status bar shows "thinking" while agent processes |
| 05-c | Agent done | Status bar clears; reply visible |

## UAT-06 — File attachments

| # | Action | Expected |
|---|--------|----------|
| 06-a | Attach image file and send | Image thumbnail visible in chat bubble |
| 06-b | Attach non-image file and send | Filename + size link visible; download works |
| 06-c | `GET /api/attachments/{id}/download` | Returns file bytes with correct content-type |
| 06-d | Agent message references attached file | Agent note about attached file prepended to prompt |

## UAT-07 — Markdown rendering

| # | Action | Expected |
|---|--------|----------|
| 07-a | Agent replies with fenced code block | Syntax-highlighted code block rendered |
| 07-b | Agent replies with markdown table | Table rendered with borders |
| 07-c | Agent replies with Mermaid diagram | Diagram rendered (not raw code) |
| 07-d | Click "expand" on long agent reply | Full content visible |
| 07-e | Click Details → shows tool calls and cost | Transcript panel opens; tool names and `$0.00xx` cost shown |
| 07-f | Toggle Raw in Details panel | Raw JSONL stream displayed |

## UAT-08 — Navigation and routing (UI)

| # | Action | Expected |
|---|--------|----------|
| 08-a | Click workspace from list | WorkspaceDetail loads with staffs panel and topic list |
| 08-b | Click topic | TopicChat loads with message history |
| 08-c | Navigate to `/settings` | Settings page loads |
| 08-d | Navigate to `/archived` | Archived workspaces page loads |
| 08-e | Navigate to `/workspaces/{id}/archived-topics` | Archived topics for that workspace load |
| 08-f | Browser back/forward | Routes work correctly (Vue Router history mode) |

## UAT-09 — Schema and DB integrity

| # | Action | Expected |
|---|--------|----------|
| 09-a | `GET /api/schema` | Returns all table names including `staffs`, `staff_sessions`, `config` |
| 09-b | Master restart on existing DB | No migration errors; existing data preserved |

---

## Sign-off

| Field | Value |
|-------|-------|
| Date | |
| Testbed | |
| Commit under test | |
| Passed | |
| Failed | |
| Blocked | |
| Sign-off | |
