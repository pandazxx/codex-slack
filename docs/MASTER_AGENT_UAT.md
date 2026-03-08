# Master-Agent UAT Test Cases (v1)

## Purpose
Validate end-to-end v1 behavior for:
- containerized deployment and runtime wiring
- master lifecycle control
- agent startup and workspace initialization
- Slack routing and thread continuity
- policy enforcement and failure handling

## v3.0 UAT Checklist (Dual Frontend + Dual Adapter)
Use this checklist for current `feat/v3.0` validation. It is additive to v1 tests.

### UAT-v3-001: Frontend Startup Matrix
Preconditions:
- Build/runtime includes `discord.py`.
- Valid tokens and admin channels are available.

Steps:
1. Run with `MASTER_FRONTENDS=slack`.
2. Run with `MASTER_FRONTENDS=discord`.
3. Run with `MASTER_FRONTENDS=slack,discord`.

Expected:
- Enabled frontends start without crashing.
- Disabled frontend tokens are not required.
- Startup logs show which frontend workers are running.

### UAT-v3-002: Registry Schema Migration
Preconditions:
- Existing `agents.json` created before v3.0 (missing `schema_version`, `platform`, `agent_adapter`).

Steps:
1. Start master once.
2. Inspect `MASTER_REGISTRY_PATH`.

Expected:
- Registry is migrated to `schema_version=2`.
- Existing agents are backfilled with:
  - `platform=slack`
  - `agent_adapter=codex`

### UAT-v3-003: Platform-Aware Agent Load (Slack Command)
Steps:
1. In Slack admin channel:
```text
/master-agent-load slack-agent REPO_URL C0123456789 main --platform slack --adapter codex
```
2. In Slack admin channel:
```text
/master-agent-load discord-agent REPO_URL 123456789012345678 main --platform discord --adapter claude-code
```

Expected:
- Both loads succeed with `ok=true`.
- Response payload contains `platform` and `agent_adapter`.
- Invalid combinations are rejected (for example Discord platform with Slack-style channel ID).

### UAT-v3-004: Discord Command Parity
Steps:
1. In Discord admin channel, run:
- `/master-agent-list`
- `/master-agent-status`
- `/master-agent-usage`
- `/master-agent-start`
- `/master-agent-stop`
- `/master-agent-remove`
- `/master-agent-refresh-auth`

Expected:
- Command behavior and response semantics match Slack command flow.
- Admin-channel enforcement uses `DISCORD_ADMIN_CHANNELS`.

### UAT-v3-005: Adapter Routing Selection
Preconditions:
- One agent loaded with `--adapter codex`.
- One agent loaded with `--adapter claude-code`.

Steps:
1. Send a routed prompt to each mapped channel.
2. Inspect master logs for dispatch command selection.

Expected:
- Codex-mapped agent uses Codex command template.
- Claude-mapped agent uses Claude command template.
- No cross-adapter leakage between agents.

### UAT-v3-006: Cross-Frontend Concurrent Routing
Preconditions:
- `MASTER_FRONTENDS=slack,discord`.
- One Slack-mapped agent and one Discord-mapped agent are running.

Steps:
1. Send prompt in Slack mapped channel.
2. Send prompt in Discord mapped channel.
3. Send follow-up message in each thread/reply context.

Expected:
- Both platforms route concurrently without blocking each other.
- Follow-up continuity works per platform thread key.
- Usage metrics increment for both flows.

### UAT-v3-007: Load Command Option Validation
Steps:
1. Try unknown option:
```text
/master-agent-load x REPO_URL C123 main --unknown foo
```
2. Try invalid adapter:
```text
/master-agent-load x REPO_URL C123 main --adapter unknown
```
3. Try invalid platform:
```text
/master-agent-load x REPO_URL C123 main --platform unknown
```

Expected:
- Command returns `ERR_INVALID_ARGS` with actionable error text.

### UAT-v3-008: Backward Compatibility (Legacy Slack Loads)
Steps:
1. Run legacy syntax in Slack:
```text
/master-agent-load legacy-agent REPO_URL C0123456789
```

Expected:
- Load still succeeds.
- Defaults are applied:
  - `platform=slack`
  - `agent_adapter=codex`

## Test Environment
- Master branch under test: current `master` (or release candidate tag under validation).
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
export GH_TOKEN=...
export MASTER_CODEX_AUTH_JSON_PATH=/absolute/host/path/auth.json
export MASTER_SSH_AUTH_SOCK_PATH=/absolute/host/path/ssh-agent.sock
export MASTER_GIT_USER_NAME='Your Name'
export MASTER_GIT_USER_EMAIL='you@example.com'
# Optional:
# export MASTER_SSH_KNOWN_HOSTS_PATH=/absolute/host/path/known_hosts
export MASTER_ADMIN_CHANNELS=C0123456789
export MASTER_AGENT_BASE_IMAGE=codex-slack-v1-uat
export MASTER_REGISTRY_PATH=data/master/agents.json
export MASTER_DRY_RUN=false
export MASTER_AGENT_COMMAND_TEMPLATE='codex exec --dangerously-bypass-approvals-and-sandbox resume --last -'
export MASTER_AGENT_TIMEOUT_SECONDS=120
export MASTER_COMMAND_RATE_LIMIT_COUNT=20
export MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60
python -m src.master.main
```
2. Ensure agent auth source exists:
- `SSH_AUTH_SOCK` mounted, or
- absolute-path `GH_TOKEN_FILE` mounted.
For the current v1 implementation, the simplest supported path is setting `GH_TOKEN` on the master process so it can be forwarded into agent containers.
3. Prepare auth-specific test fixtures for Git operations:
- one private repo reachable over SSH (for checkout validation)
- one writable test repo/branch reachable over SSH (for push validation)
- one valid GitHub token with enough scope for `gh auth status` and repo metadata checks
- host `SSH_AUTH_SOCK` must reference an agent loaded with the correct key when running SSH-based UAT cases
- the host path exported in `MASTER_SSH_AUTH_SOCK_PATH` must be valid for the host Podman service
- `MASTER_SSH_KNOWN_HOSTS_PATH` is optional; if omitted, SSH will accept all hosts by default

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
The master image must include the `podman` CLI binary. The mounted socket alone is not sufficient because the master runtime shells out to `podman ...` for build/create/start/stop/inspect operations.
2. Start host Podman service/socket and confirm socket path.
3. Prefer the rootless user socket when running the master container as a non-root process:
```bash
ls -l /run/user/$(id -u)/podman/podman.sock
```
4. Run master in container with Podman socket mounted:
```bash
podman run --rm \
  --userns=keep-id \
  --security-opt label=disable \
  -e SLACK_BOT_TOKEN \
  -e SLACK_APP_TOKEN \
  -e GH_TOKEN \
  -e SSH_AUTH_SOCK=/ssh-agent \
  -e MASTER_GIT_USER_NAME='Your Name' \
  -e MASTER_GIT_USER_EMAIL='you@example.com' \
  -e MASTER_CODEX_AUTH_JSON_PATH=/absolute/host/path/auth.json \
  -e MASTER_SSH_AUTH_SOCK_PATH=/absolute/host/path/ssh-agent.sock \
  -e MASTER_ADMIN_CHANNELS=C0123456789 \
  -e MASTER_AGENT_BASE_IMAGE=codex-slack-v1-uat \
  -e MASTER_REGISTRY_PATH=/opt/codex-slack/data/master/agents.json \
  -e MASTER_AGENT_COMMAND_TEMPLATE='codex exec --dangerously-bypass-approvals-and-sandbox resume --last -' \
  -e MASTER_AGENT_TIMEOUT_SECONDS=120 \
  -e MASTER_COMMAND_RATE_LIMIT_COUNT=20 \
  -e MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60 \
  -e CODEX_CONTAINER_MODE=bot \
  -v "$(pwd)/data/master:/opt/codex-slack/data/master" \
  -v /absolute/host/path/ssh-agent.sock:/ssh-agent \
  -v /run/user/$(id -u)/podman/podman.sock:/run/podman/podman.sock \
  -e CONTAINER_HOST=unix:///run/podman/podman.sock \
  codex-slack-v1-uat \
  python -m src.master.main
```
5. Use the rootful socket only if you intentionally run the master container with matching privileges.

Why this matters:
- `MASTER_AGENT_BASE_IMAGE` controls which base image the master uses for default-image agents; if you rebuild `codex-slack-v1-uat` but leave this unset, the master still creates agents from `codex-slack-bot:latest`.
- `GH_TOKEN` on the master is forwarded into agent containers so worker `preflight` can pass and `git clone` can authenticate.
- `MASTER_CODEX_AUTH_JSON_PATH` mounts only the shared Codex `auth.json` into agents. V1 does not forward Codex session directories into agents.
- `MASTER_SSH_AUTH_SOCK_PATH` mounts the shared SSH agent socket into agents so private repo checkout and push can use the same loaded key material as the master host.
- The master container itself also needs the SSH socket mounted (for example to `/ssh-agent`) and `SSH_AUTH_SOCK=/ssh-agent` so `/master-agent-load` can use the same agent for private SSH clones.
- If `MASTER_SSH_KNOWN_HOSTS_PATH` is set, it mounts a host `known_hosts` file into agents for explicit SSH host verification.
- If `MASTER_SSH_KNOWN_HOSTS_PATH` is omitted, master and agents default to `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null` to accept all SSH hosts.
- `MASTER_GIT_USER_NAME` and `MASTER_GIT_USER_EMAIL` are passed into agents and written into the checked-out repo's local Git config during worker startup so commit flows inherit the master's configured identity.
- Routed agent prompts now use a stable per-thread session id, so repeated messages in the same Slack thread should retain conversation context.
- Default `MASTER_AGENT_COMMAND_TEMPLATE` is `codex exec --dangerously-bypass-approvals-and-sandbox resume --last -`.
- `--userns=keep-id` preserves the host UID/GID so the container process can open the rootless Podman socket owned by your user.
- `--security-opt label=disable` avoids SELinux relabel restrictions blocking socket access on host-mounted Unix sockets.
- Mounting `$(pwd)/data/master` into `/opt/codex-slack/data/master` persists `agents.json` across master container restarts.
- Mounting the rootless socket to `/run/podman/podman.sock` keeps the in-container `CONTAINER_HOST` stable.

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
- No `podman CLI is not installed in the master runtime` errors.
- Master process inside container reaches steady state.
- No `unable to connect to Podman socket ... permission denied` errors when starting agents.

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
- Rootless socket access works when launched with `--userns=keep-id --security-opt label=disable`.

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
- If branch is omitted, load tries `main` first and falls back to `master`.
- Optional explicit branch example: `/master-agent-load test-agent1 pandazxx/touchfish_agent C0987654321 master`

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

## UAT-005A: Refresh Agent Auth From Host
Preconditions:
- Agent is loaded.
- `MASTER_CODEX_AUTH_JSON_PATH` points to the host auth file.
- Host auth file has been refreshed (for example, `codex login` completed on the host).

Steps:
1. In `CADMIN`, run:
```text
/master-agent-refresh-auth payments-agent
```
2. Inspect the response payload.

Expected:
- `ok=true`, `code=OK`.
- Response includes `refreshed=true`.
- Response includes the target workspace volume name.
- Existing agent workspace `.codex` is re-seeded from the current host `auth.json` without removing the agent record.

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
podman exec <agent-container> cat /tmp/master-agent/status.json
```

Expected:
- Structured `agent.stage` log lines are present.
- Status file reflects current stage or failure stage.

## UAT-014C: Private Repo Checkout Via Shared SSH Key
Preconditions:
- SSH-based agent auth forwarding is enabled for the environment under test.
- Host `SSH_AUTH_SOCK` has the correct private key loaded.
- A private test repo is available over SSH URL (for example `git@github.com:org/private-repo.git`).

Steps:
1. Load the agent using the private SSH repo URL.
2. Start the agent.
3. Inspect agent logs and status.
4. If needed, verify the checked-out repo exists inside the agent workspace:
```bash
podman exec <agent-container> sh -lc 'cd /workspace/repo && git remote -v && git rev-parse --is-inside-work-tree'
```

Expected:
- Agent preflight passes with the shared SSH agent/key.
- Repo sync succeeds against the private SSH remote.
- Agent reaches `running` state without falling back to token-only auth.

## UAT-014D: Git Push Via Shared SSH Key
Preconditions:
- UAT-014C passed.
- The private key loaded in `SSH_AUTH_SOCK` has push permission to a safe test repo/branch.
- A disposable branch name is selected for the test.

Steps:
1. Exec into the agent container:
```bash
podman exec -it <agent-container> sh
```
2. Create a disposable branch and test commit from `/workspace/repo`.
3. Push the branch to origin over SSH:
```bash
git checkout -b uat/master-agent-ssh-push
printf 'uat\n' >> .uat-push-check
git add .uat-push-check
git commit -m 'chore: uat ssh push check'
git push -u origin uat/master-agent-ssh-push
```
4. Clean up the branch on the remote after verification.

Expected:
- `git push` succeeds over SSH from inside the agent container.
- No interactive credential prompt appears.
- The agent uses the shared SSH agent/key rather than failing with permission denied.

## UAT-014E: GitHub CLI Token Availability In Agent Container
Preconditions:
- `GH_TOKEN` (or equivalent supported GitHub token path) is configured for agent use.
- Agent container is running.

Steps:
1. Verify token presence in the agent runtime:
```bash
podman exec <agent-container> sh -lc 'env | grep -E "^(GH_TOKEN|GITHUB_TOKEN)="'
```
2. Verify GitHub CLI auth works:
```bash
podman exec <agent-container> sh -lc 'gh auth status'
```
3. Verify the token can access expected repo metadata:
```bash
podman exec <agent-container> sh -lc 'gh repo view <owner>/<repo> --json name,visibility'
```

Expected:
- The expected GitHub token env var is present inside the agent container.
- `gh auth status` succeeds.
- `gh repo view` succeeds for an intended test repo, confirming the token scope is usable for agent workflows.

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
