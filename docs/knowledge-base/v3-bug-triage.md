# v3 Bug Triage Report

**Date:** 2026-05-02
**Branch:** release/v3-slice-12
**Scope:** Pre-v3 open issues + closed bug reports + fix commits, assessed against the v3 FastAPI/MQTT/SQLite/Vue 3 codebase.

---

## 1. Issues confirmed NOT applicable to v3

These issues were filed against the pre-v3 Slack-bot / Discord-bot / CLI-bridge architecture, which no longer exists in v3.

| # | Title | Reason irrelevant |
|---|-------|-------------------|
| #25 | `/master-agent-set-model` missing in Discord/Slack | v3 has no Slack/Discord frontend; commands are via the Vue UI |
| #26 | PR description mentions "Generated with Claude Code" | Codex skill issue, not a v3 system bug |
| #29 | `MASTER_CODEX_CONFIG_DIR_PATH` not handled properly | Pre-v3 config copy mechanism; v3 does not use this env var |
| #36 | Per-channel usage stats | v3 has no channel concept; tracked via topics |
| #37 | Auto channel creation for agent provisioning | v3 has no Slack/Discord channels |
| #38 | Separate base agent image | Addressed: `Dockerfile.agent-minimal` exists |
| #39 | Message split hint | Pre-v3 Slack message formatting feature |
| #40 | codex skill yaml error | Skill YAML is pre-v3 infra |
| #45 | Include frontend in container name | Pre-v3 bot container naming |
| #46 | claude container config not copied | Pre-v3 config-copy mechanism; v3 uses named Docker volumes |
| #51 | Start agent if not started when user messages | v3 agents are always running (respawned on master start) |
| #52 | `gh` not available in minimal agent image | Fixed: `gh` is installed in `Dockerfile.agent-minimal` |
| #53 | Remove workspace volume support in later phases | v3 does not mount workspace config; N/A |
| #54 | Remove mounted configuration support | Pre-v3 concern; v3 uses env vars only |
| #55 | Remove codex session passthrough | Pre-v3; codex in v3 runs statelessly per-call |
| #56 | Repo force update during agent startup is dangerous | Pre-v3; v3 agent containers clone/pull at startup via `AGENT_REPO_URL` |
| #30 | Tag skill doesn't work correctly | Skill issue, not a v3 system bug |
| #31 | Evaluate docx2python | Backlog research item; no v3 attachment handling yet |
| #32 | Support follow-up turns against previously uploaded docs | No file upload feature in v3 yet |

---

## 2. Issues confirmed FIXED in v3

These bugs were filed pre-v3 and the v3 implementation explicitly addresses them.

### #17 — claude code doesn't skip permission prompts
**Status: Fixed.**
`src/agent/mqtt_loop.py` passes `--dangerously-skip-permissions` to every `claude` invocation (commit `07896bf`). The flag is present and correct.

### #18 / #59 — Claude Code sessions not resuming across follow-up prompts
**Status: Fixed.**
- v3 stores `llm_session_id` per `(topic_id, agent_name)` in the `sessions` table (`src/master/db.py`).
- `send_message` in `src/master/messages.py` calls `_get_or_create_session` to retrieve the stored `llm_session_id` and passes it in the MQTT prompt payload.
- The agent in `src/agent/mqtt_loop.py` picks up `session_id` from the payload and passes `--resume <id>` to claude.
- On session expiry, `_run_claude` automatically retries without `--resume` (commit `caa5e15`).
- Claude session state is persisted across container restarts via named Docker volume `codex-claude-{workspace_id}` mounted at `/home/appuser/.claude` (commit `caa5e15`).

### #42 — Stale cached base images during project-specific builds
**Status: Not applicable in v3.**
v3 uses `docker.from_env()` Python SDK to spawn containers from a pre-pulled base image. There is no project-local `Dockerfile` build step in the current v3 flow. If custom images are introduced later, `--pull=newer` should be added at that time.

### #16 — Prompt acknowledgement delay
**Status: Mitigated.**
v3 publishes a `{"state": "thinking"}` status message via MQTT immediately when the prompt is received, before any LLM call begins. The frontend WebSocket receives this and can show a loading indicator. The "acknowledgement delay" is structurally resolved.

### #62 — Discord image attachment staging fails with 403
**Status: Not applicable.**
v3 has no Discord frontend. Attachment staging via Discord CDN URLs is not a concern in the current architecture.

---

## 3. Open issues still applicable to v3

### #60 — Durable Claude session lifecycle (enhancement)
**Priority: Medium.**
The current fix (issue #59) uses in-memory channel-based tracking replaced in v3 by DB-persisted `llm_session_id`. However, issue #60 raises longer-term questions that remain open:
- Sessions are not reconciled when a container is recreated with a different workspace volume.
- Stale sessions after container recreation will self-heal via the retry (`_SESSION_NOT_FOUND` path) but silently lose conversation history.
- No operator visibility into active sessions.
- No cleanup/GC of sessions for archived topics.

### #42 — Stale base image caches (if custom agent images are added)
**Priority: Low (future risk).**
If project-specific Dockerfiles are added back (see ADR #0002), `spawn_agent` in `agent_runner.py` must use `--pull=newer` or equivalent SDK option.

---

## 4. Pre-v3 fix commits assessed for regression in v3

The following `fix:` commits were made in the pre-v3 era. Each has been checked against the current v3 source.

| Commit | Summary | v3 Status |
|--------|---------|-----------|
| `e0458a5` | persist claude channel session state | Superseded by DB-backed sessions table |
| `5eae32d` | use claude named session flags | Superseded by `--resume` in `mqtt_loop.py` |
| `909846f` | prefer discord proxy attachment URLs | Not applicable — no Discord in v3 |
| `c22b8ef` | issues 53/56 agent runtime cleanups | Superseded by v3 agent_runner.py |
| `c974ac0` | auto-start stopped agent containers on message | Superseded by respawn-on-startup in `main.py` |
| `7b7288d` | require master auto-start for stopped agents | Same as above |
| `2e4066d` | create writable workspace in agent images | Present: `/workspace/home` created in both Dockerfiles |
| `d7e1835` | refresh stale agent auth before dispatch | Not implemented in v3 — potential gap if auth tokens expire |
| `4606259` / `4b7bb91` / `8156f48` | wait for agent repo workdir before dispatch | Partially addressed: v3 creates worktrees lazily in `_ensure_worktree`; no explicit poll/wait |

---

## 5. Bugs found and fixed during this triage

### Bug A — `Dockerfile.agent-minimal` missing `/home/appuser/.claude` pre-creation

**Root cause:** The fix from commit `51c4e40` was applied only to `Dockerfile` (the master image), not to `Dockerfile.agent-minimal` (the agent worker image actually used for spawned containers). Docker initialises a named volume's bind point as a root-owned directory if the target path does not exist in the image. The `codex-claude-{workspace_id}` volume is mounted at `/home/appuser/.claude`; if that directory is root-owned in the container, claude-code cannot write session files there, causing every prompt to start a new session.

**Fix applied:** Added `mkdir -p /home/appuser/.claude` and `chown` in `Dockerfile.agent-minimal`, mirroring the fix already in `Dockerfile`.

**File changed:** `Dockerfile.agent-minimal`

---

### Bug B — `_respawn_agents` respawns archived workspaces on master restart

**Root cause:** The SQL query in `src/master/main.py::_respawn_agents` selected all workspaces with a non-null `container_name`, without filtering `archived_at IS NULL`. After a master restart, archived workspaces (which have had their container stopped) would be respawned unnecessarily.

**Fix applied:** Added `AND archived_at IS NULL` to the query.

**File changed:** `src/master/main.py`

---

### Bug C — `send_message` accepts messages to archived workspaces and topics

**Root cause:** The workspace-existence check in `src/master/messages.py::send_message` used `WHERE id = ?` without `AND archived_at IS NULL`. An archived workspace's container has been stopped, so any MQTT prompt published for it will never be answered, leaving the message stuck in "queued" state forever. Similarly, the topic lookup did not filter `archived_at IS NULL`, so archived topics could receive new messages.

**Fix applied:** Added `AND archived_at IS NULL` to both the workspace lookup and the topic lookup in `send_message`.

**File changed:** `src/master/messages.py`

---

## 6. Known gaps not yet fixed (logged for future work)

- **Auth token refresh on dispatch** — pre-v3 had a `refresh stale agent auth before dispatch` mechanism (`d7e1835`). v3 does not re-implement this. If `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` expire while the agent container is running, the agent will fail silently. Recommendation: add health-check / token-refresh logic or document a re-spawn procedure.
- **Session GC for archived topics** — sessions rows are never deleted when a topic is archived. The `sessions` table will accumulate stale rows over time. A cleanup task or cascade delete on topic archive should be added.
- **No explicit wait for worktree readiness** — the pre-v3 codebase polled for the workdir to be ready before dispatching. v3 creates worktrees lazily inside the agent process, but a race exists if the first message arrives before the repo clone has completed. Worktree creation errors are caught and logged, and the CWD falls back to `repo_dir` or `/`, which may produce an unexpected response rather than a clear error.
