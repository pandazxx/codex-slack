# Test Plan — v3 Slice 14: pdftoppm, Token Usage, Auth Refresh, Auto Start/Stop

**Issues:** #94 · #85 · #88 · #84 · #83
**Branch:** `feat/enhancements-v3-slice-14`
**PR:** #99

---

## Deployment (run on testbed before executing cases)

```bash
cd /opt/codex-slack
git fetch origin
git checkout feat/enhancements-v3-slice-14
git pull origin feat/enhancements-v3-slice-14
docker compose build --no-cache master
docker compose up -d --force-recreate master
# Remove and re-create agent containers so the new image is used:
docker ps --format '{{.Names}}' | grep codex-agent | xargs -r docker rm -f
docker compose up -d --force-recreate master   # _respawn_agents spawns fresh containers
```

Verify schema has new columns:
```bash
curl -s http://10.10.10.123:8080/schema | python3 -m json.tool
# workspaces must include: last_refreshed_at, last_message_at
# messages must include: usage_json
```

---

## UAT-01 — pdftoppm available in agent container (#94)

| # | Action | Expected |
|---|--------|----------|
| 01-a | `docker exec codex-agent-<id> pdftoppm -v 2>&1` | Prints pdftoppm version, no "not found" error |
| 01-b | `docker exec codex-agent-<id> which pdfinfo` | Returns `/usr/bin/pdfinfo` (poppler-utils includes pdfinfo) |

## UAT-02 — Schema migration (#85 #88 #84)

| # | Action | Expected |
|---|--------|----------|
| 02-a | `GET /schema` | `workspaces` cols include `last_refreshed_at`, `last_message_at` |
| 02-b | `GET /schema` | `messages` cols include `usage_json` |

## UAT-03 — Context window token usage in detail panel (#85)

| # | Action | Expected |
|---|--------|----------|
| 03-a | Send a message to any topic; wait for agent reply | Agent message saved with `usage_json` (non-null in DB) |
| 03-b | `GET /api/workspaces/{id}/topics/{id}/messages` | Agent message includes `usage_json` field with `input_tokens`, `output_tokens` |
| 03-c | Open topic in browser, open Details on agent message | Result footer shows e.g. `1 234↑  89↓` token counts in indigo |
| 03-d | If response used cache, footer shows cache stats | `XX cached` shown in teal |

## UAT-04 — Auth token refresh endpoint (#88)

| # | Action | Expected |
|---|--------|----------|
| 04-a | `POST /api/workspaces/{id}/refresh-auth` | 200; body `{"refreshed_at": "2026-..."}` |
| 04-b | `GET /api/workspaces/{id}` after refresh | `last_refreshed_at` field is set and matches response |
| 04-c | `POST /api/workspaces/nonexistent/refresh-auth` | 404 |
| 04-d | `POST /api/workspaces/<archived-id>/refresh-auth` | 404 |

## UAT-05 — Refresh Auth UI (#88)

| # | Steps | Expected |
|---|-------|----------|
| M-01 | Open any workspace in browser | "Refresh Auth" button visible next to container status badge |
| M-02 | Click "Refresh Auth" | Button shows "Refreshing…", then returns to "Refresh Auth"; `refreshed YYYY-MM-DDT…` timestamp appears |
| M-03 | Reload the page after refresh | `last_refreshed_at` timestamp still shown |

## UAT-06 — Auto-start stopped container on message send (#84)

| # | Action | Expected |
|---|--------|----------|
| 06-a | `docker stop codex-agent-<id>` | Container status changes to "exited" |
| 06-b | Send a message to any topic in that workspace | 202; container auto-starts (check `docker ps` — status `running`) |
| 06-c | Agent eventually replies | Message with `sender=agent` appears |

## UAT-07 — Health-check respawn of crashed container (#84)

| # | Action | Expected |
|---|--------|----------|
| 07-a | `docker kill --signal=SIGKILL codex-agent-<id>` | Container enters `exited` state with non-zero exit code |
| 07-b | Wait up to 90 seconds | Background task detects non-zero exit and calls `container.start()`; container status returns to `running` |
| 07-c | `GET /api/workspaces/{id}/agent-status` | `{"status": "running", …}` |

## UAT-08 — Idle auto-stop (#83)

| # | Setup | Action | Expected |
|---|-------|--------|----------|
| 08-a | Set env `AGENT_IDLE_TIMEOUT_SECONDS=120` and restart master | Wait 3 min without sending a message | Container for that workspace is stopped (`docker ps` — no longer running) |
| 08-b | Send a message to the stopped workspace | 202; container auto-starts again before MQTT publish |

## UAT-09 — last_message_at tracking (#83)

| # | Action | Expected |
|---|--------|----------|
| 09-a | Send a message to a workspace | `SELECT last_message_at FROM workspaces WHERE id='...'` in DB returns current timestamp |
| 09-b | Send another message | `last_message_at` updated to new timestamp |

---

## Testbed execution log

*To be filled in after execution.*

| Field | Value |
|-------|-------|
| Date | |
| Testbed | 10.10.10.123 |
| Commit under test | 9057ff280eaa7946a0f1bc4f6b34e7945855771f |
| UAT-01 | |
| UAT-02 | |
| UAT-03 | |
| UAT-04 | |
| UAT-05 (manual) | |
| UAT-06 | |
| UAT-07 | |
| UAT-08 | |
| UAT-09 | |
| Sign-off | |
