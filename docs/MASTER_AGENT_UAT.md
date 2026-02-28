# Master-Agent UAT Test Cases (v1)

## Purpose
Validate end-to-end v1 behavior for:
- containerized deployment and runtime wiring
- master lifecycle control
- agent startup and workspace initialization
- Slack routing and thread continuity
- policy enforcement and failure handling

## Test Environment
- Master branch under test: `feat/master-agent-phase1-impl` build.
- One Slack workspace with:
- admin channel (example channel ID placeholder: `CADMIN`)
- mapped agent channel (example channel ID placeholder: `CAGENT`)
- one non-admin non-mapped channel (example channel ID placeholder: `COTHER`)
- Podman host socket mounted into master runtime.
- Network access to a test repo (`REPO_URL`).

Important:
- `CADMIN`, `CAGENT`, and `COTHER` in this document are placeholder Slack channel IDs.
- Replace them with real channel IDs such as `C0123456789`.
- Do not use Slack channel names like `cadmin` or `cagent` in these commands.

## Shared Setup
1. Export master env and start master:
```bash
export SLACK_BOT_TOKEN=...
export SLACK_APP_TOKEN=...
export MASTER_ADMIN_CHANNELS=C0123456789
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

## Container Deployment Requirement
Containerized validation is mandatory for v1 sign-off.

At minimum, UAT must verify:
- master runs inside a container
- master can reach host Podman through mounted socket
- master creates and controls real agent containers
- agent worker initialization and status are observed through container runtime behavior

## Containerized UAT Setup
1. Build the current image under test:
```bash
podman build -t codex-slack-v1-uat .
```
2. Start host Podman service/socket and confirm socket path.
3. Run master in container with Podman socket mounted:
```bash
podman run --rm \
  -e SLACK_BOT_TOKEN \
  -e SLACK_APP_TOKEN \
  -e MASTER_ADMIN_CHANNELS=C0123456789 \
  -e MASTER_REGISTRY_PATH=/opt/codex-slack/data/master/agents.json \
  -e MASTER_AGENT_COMMAND_TEMPLATE='codex exec -' \
  -e MASTER_AGENT_TIMEOUT_SECONDS=120 \
  -e MASTER_COMMAND_RATE_LIMIT_COUNT=20 \
  -e MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60 \
  -e CODEX_CONTAINER_MODE=bot \
  -v /run/podman/podman.sock:/run/podman/podman.sock \
  -e CONTAINER_HOST=unix:///run/podman/podman.sock \
  codex-slack-v1-uat \
  python -m src.master.main
```
4. Use equivalent rootless socket path if required by host setup.

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

## UAT-001A: Master Container Startup
Preconditions:
- Containerized UAT setup complete.

Steps:
1. Start master in container using mounted Podman socket.
2. Confirm container stays running.
3. Inspect master container logs.

Expected:
- Master container starts successfully.
- No socket/path permission errors.
- Master process inside container reaches steady state.

## UAT-001B: Podman Socket Reachability From Master Container
Preconditions:
- Master container running.

Steps:
1. From host, confirm mounted socket path matches `CONTAINER_HOST`.
2. Trigger a lifecycle command such as `/master-agent-list`.
3. Trigger `/master-agent-load` and `/master-agent-start` for a test agent.

Expected:
- Master command path does not fail due to Podman connectivity.
- Container operations execute through host Podman, not nested runtime.

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
- Valid repo source and target agent channel ID (example placeholder: `CAGENT`).
- Supported repo source examples:
- `pandazxx/aidotfile`
- `https://github.com/pandazxx/aidotfile.git`
- `/absolute/local/path/to/repo`

Steps:
1. In `CADMIN`, run:
```text
/master-agent-load payments-agent REPO_URL C0987654321
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

## UAT-004A: Real Agent Container Creation
Preconditions:
- Containerized master running.
- UAT-003 completed.

Steps:
1. Run `/master-agent-start payments-agent`.
2. On host, inspect Podman containers:
```bash
podman ps -a
```

Expected:
- A real agent container exists (for example `agent-payments-agent`).
- Container state reflects the command result (`running` or failed with inspectable state).

## UAT-004B: Project Dockerfile Build From Master Container
Preconditions:
- Test repo contains `.prj_assistant/image/Dockerfile`.

Steps:
1. Load the project.
2. Start the agent.
3. Check host images:
```bash
podman images
```

Expected:
- Build is triggered by `start`.
- A built image for that agent appears on host Podman.
- No build occurs during `load`.

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

## UAT-014A: Agent Container Mount Visibility
Preconditions:
- Containerized master starts a real agent container.
- Agent relies on mounted auth source.

Steps:
1. Start agent with mounted `SSH_AUTH_SOCK` or absolute `GH_TOKEN_FILE`.
2. Inspect worker status and logs.
3. If needed, exec into container and verify path exists.

Expected:
- Mounted auth source is visible inside agent container.
- Worker preflight passes with valid mount.

## UAT-014B: Agent Status File and Runtime Logs
Preconditions:
- Agent container started or failed during init.

Steps:
1. Inspect agent container logs:
```bash
podman logs <agent-container>
```
2. Inspect status file path inside agent container if container is still available:
```bash
podman exec <agent-container> cat /run/master-agent/status.json
```

Expected:
- Structured `agent.stage` log lines are present.
- Status file reflects current stage or failure stage.

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

## UAT-018: Rootful vs Rootless Podman Socket Check
Preconditions:
- Access to environments using rootful and/or rootless Podman.

Steps:
1. Run containerized master against rootful socket path.
2. Repeat against rootless socket path.
3. Execute `load -> start -> status` in each mode.

Expected:
- Master works with the selected socket mode when `CONTAINER_HOST` matches mounted path.
- Any socket-mode-specific failure is documented before sign-off.

## UAT Sign-Off Template
- Date:
- Environment (host, Slack workspace):
- Build/commit under test:
- Passed cases:
- Failed cases:
- Blocking issues:
- Sign-off owner:
