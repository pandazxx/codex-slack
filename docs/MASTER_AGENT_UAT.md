# Master-Agent UAT Test Cases (v1)

## Purpose
Validate end-to-end v1 behavior for:
- master lifecycle control
- agent startup and workspace initialization
- Slack routing and thread continuity
- policy enforcement and failure handling

## Test Environment
- Master branch under test: `feat/master-agent-phase1-impl` build.
- One Slack workspace with:
- admin channel (`CADMIN`)
- mapped agent channel (`CAGENT`)
- one non-admin non-mapped channel (`COTHER`)
- Podman host socket mounted into master runtime.
- Network access to a test repo (`REPO_URL`).

## Shared Setup
1. Export master env and start master:
```bash
export SLACK_BOT_TOKEN=...
export SLACK_APP_TOKEN=...
export MASTER_ADMIN_CHANNELS=CADMIN
export MASTER_REGISTRY_PATH=data/master/agents.json
export MASTER_DRY_RUN=false
export MASTER_AGENT_COMMAND_TEMPLATE='codex exec -'
export MASTER_AGENT_TIMEOUT_SECONDS=120
export MASTER_COMMAND_RATE_LIMIT_COUNT=20
export MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60
python -m src.master.main
```
2. Ensure agent auth source exists:
- `SSH_AUTH_SOCK` mounted, or
- absolute-path `GH_TOKEN_FILE` mounted.

## UAT-001: Master Startup
Preconditions:
- Shared setup complete.

Steps:
1. Start master process.
2. Observe logs for startup errors.

Expected:
- Master process stays running.
- No missing-env startup exceptions.
- Logs show command handlers initialized.

## UAT-002: List Agents (Empty)
Preconditions:
- Fresh registry (`data/master/agents.json` absent or empty).

Steps:
1. In `CADMIN`, run:
```text
/master-agent-list
```

Expected:
- JSON response with `ok=true`.
- `data.agents` is empty list.

## UAT-003: Load Agent
Preconditions:
- Valid `REPO_URL` and target channel `CAGENT`.

Steps:
1. In `CADMIN`, run:
```text
/master-agent-load payments-agent REPO_URL CAGENT
```

Expected:
- `ok=true`, `code=OK`.
- Response includes `state=loaded` and image plan.
- Registry entry created for `payments-agent`.

## UAT-004: Start Agent
Preconditions:
- UAT-003 completed.

Steps:
1. In `CADMIN`, run:
```text
/master-agent-start payments-agent
```
2. In `CADMIN`, run:
```text
/master-agent-status payments-agent
```

Expected:
- Start returns `ok=true`.
- Status shows runtime/container data.
- If `.prj_assistant/image/Dockerfile` exists, build occurs at start.

## UAT-005: Stop and Remove Agent
Preconditions:
- Agent running from UAT-004.

Steps:
1. In `CADMIN`, run `/master-agent-stop payments-agent`.
2. In `CADMIN`, run `/master-agent-remove payments-agent`.

Expected:
- Stop returns `ok=true`, state `stopped`.
- Remove returns `ok=true`, `removed=true`.
- Agent no longer listed in `/master-agent-list`.

## UAT-006: Admin Channel Enforcement
Preconditions:
- Master running.

Steps:
1. In `COTHER` (non-admin), run:
```text
/master-agent-list
```

Expected:
- Command rejected.
- Response includes admin-channel-only error.

## UAT-007: Channel Mapping Conflict
Preconditions:
- Agent A loaded on `CAGENT`.

Steps:
1. Load another agent B using same channel:
```text
/master-agent-load billing-agent REPO_URL CAGENT
```

Expected:
- Response `ok=false` with `ERR_CHANNEL_CONFLICT`.

## UAT-008: Manual Rebind Workflow
Preconditions:
- Conflict state from UAT-007.

Steps:
1. Remove old agent mapping:
```text
/master-agent-remove payments-agent
```
2. Load new mapping:
```text
/master-agent-load billing-agent REPO_URL CAGENT
```

Expected:
- Second load succeeds.
- `CAGENT` bound to `billing-agent`.

## UAT-009: Mention Routing in Mapped Channel
Preconditions:
- Agent running and mapped to `CAGENT`.

Steps:
1. In `CAGENT`, send `@bot <prompt>`.

Expected:
- Master routes prompt to mapped agent.
- Bot replies in thread with agent output.

## UAT-010: Thread Continuity Without Re-Mention
Preconditions:
- UAT-009 produced a thread.

Steps:
1. Reply in same thread without mention.

Expected:
- Reply routed to same mapped agent.
- Bot responds in same thread.

## UAT-011: Unmapped Channel Behavior
Preconditions:
- `COTHER` has no mapping.

Steps:
1. In `COTHER`, mention bot with prompt.

Expected:
- No agent processing occurs.
- Master logs route skipped/unmapped channel.

## UAT-012: Invalid Repo Path Handling
Preconditions:
- Master running.

Steps:
1. In `CADMIN`, run:
```text
/master-agent-load bad-agent /nonexistent/path CAGENT
```

Expected:
- `ok=false`, `ERR_REPO_NOT_ALLOWED`.

## UAT-013: Runtime Failure Handling
Preconditions:
- Create intentionally failing start condition (for example invalid Dockerfile in project override).

Steps:
1. Load agent.
2. Start agent.
3. Check status.

Expected:
- Start returns `ERR_RUNTIME_FAILED`.
- Registry state moves to `error` with `last_error`.

## UAT-014: Worker Preflight Token File Validation
Preconditions:
- Agent worker uses `GH_TOKEN_FILE`.

Steps:
1. Start agent with relative `GH_TOKEN_FILE` path.
2. Retry with absolute missing path.
3. Retry with valid absolute file path.

Expected:
- Relative path fails preflight.
- Missing absolute path fails preflight.
- Valid absolute file passes preflight.

## UAT-015: Rate Limiting
Preconditions:
- Set low limits for test:
```bash
export MASTER_COMMAND_RATE_LIMIT_COUNT=2
export MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60
```
- Restart master.

Steps:
1. Send same admin command rapidly >2 times as same user.

Expected:
- First commands succeed.
- Subsequent command returns `ERR_RATE_LIMITED`.

## UAT-016: Registry Lock File and Audit Logs
Preconditions:
- Run at least one load/start/stop operation.

Steps:
1. Verify files:
```bash
ls data/master/agents.json data/master/agents.json.lock
```
2. Check logs for audit lines:
- `master.audit ...`
- `master.command_received ...`

Expected:
- Lock file exists.
- Audit logs present for operations.

## UAT-017: End-to-End Smoke
Preconditions:
- Clean state.

Steps:
1. `/master-agent-load payments-agent REPO_URL CAGENT`
2. `/master-agent-start payments-agent`
3. In `CAGENT`, mention bot with prompt.
4. Reply in thread without mention.
5. `/master-agent-stop payments-agent`
6. `/master-agent-remove payments-agent`

Expected:
- Full flow succeeds without manual intervention.
- No stale mapping remains after remove.

## UAT Sign-Off Template
- Date:
- Environment (host, Slack workspace):
- Build/commit under test:
- Passed cases:
- Failed cases:
- Blocking issues:
- Sign-off owner:
