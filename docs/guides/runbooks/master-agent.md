# Master-Agent Runbook (v3.0)

## Scope
Operational guide for the master->agent v3.0 stack:
- master in container (Slack + Discord + orchestration)
- agent worker containers (no direct Slack connection)
- Podman host socket control path

See `docs/test-plans/master-agent-uat.md` for step-by-step user acceptance test cases.
Containerized UAT is required for v3.0 sign-off.

## Prerequisites
- Host Podman service socket mounted into master container.
- For rootless Podman, mount `/run/user/<uid>/podman/podman.sock` and run the master container with `--userns=keep-id --security-opt label=disable`.
- `podman` CLI installed inside the master image/container.
- Provide `GH_TOKEN` on the master container so it can be forwarded into agent workers for repo access.
- For `claude-code` adapter agents, prefer `CLAUDE_CODE_OAUTH_TOKEN` on the master container for headless subscription auth.
- Use `ANTHROPIC_API_KEY` only for Claude Console/API billing flows, and only when `CLAUDE_CODE_OAUTH_TOKEN` is absent.
- Provide `MASTER_CODEX_AUTH_JSON_PATH` as a host path to the shared Codex `auth.json`; v1 forwards only this auth file to agents, not Codex session directories.
- Provide `MASTER_SSH_AUTH_SOCK_PATH` as a host path to the SSH agent socket for private repo checkout and push over SSH.
- Optional: provide `MASTER_SSH_KNOWN_HOSTS_PATH` as a host path to `known_hosts` for explicit SSH host verification. If omitted, master and agents default to `StrictHostKeyChecking=no` with `/dev/null` known hosts.
- Optional: provide `MASTER_CLAUDE_CONFIG_DIR_PATH` as a host path to a directory containing `settings.json` (typically `~/.claude`). Mounted into each agent as `/run/secrets/master_claude_config:ro` with `CLAUDE_CONFIG_DIR` set so claude picks up the model and other settings from there. Editing the host file takes effect on the next prompt dispatch without any restart.
- Slack and/or Discord frontend configured.
- `MASTER_FRONTENDS` set (`slack`, `discord`, or `slack,discord`).
- For Slack frontend: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `MASTER_ADMIN_CHANNELS`.
- For Discord frontend: `DISCORD_BOT_TOKEN`, `DISCORD_ADMIN_CHANNELS`.
- Shared auth refs available to agents (`SSH_AUTH_SOCK` and/or `GH_TOKEN_FILE`).

## Master Startup
1. Start master runtime. For containerized UAT, use the verified rootless Podman pattern:
```bash
podman run --rm \
  --userns=keep-id \
  --security-opt label=disable \
  -e SLACK_BOT_TOKEN \
  -e SLACK_APP_TOKEN \
  -e GH_TOKEN \
  -e CLAUDE_CODE_OAUTH_TOKEN \
  -e SSH_AUTH_SOCK=/ssh-agent \
  -e MASTER_GIT_USER_NAME='Your Name' \
  -e MASTER_GIT_USER_EMAIL='you@example.com' \
  -e MASTER_FRONTENDS=slack,discord \
  -e MASTER_CODEX_AUTH_JSON_PATH=/absolute/host/path/auth.json \
  -e MASTER_SSH_AUTH_SOCK_PATH=/absolute/host/path/ssh-agent.sock \
  -e MASTER_SSH_KNOWN_HOSTS_PATH=/absolute/host/path/known_hosts \
  -e MASTER_ADMIN_CHANNELS=<admin_channel_id> \
  -e DISCORD_ADMIN_CHANNELS=<discord_admin_channel_id> \
  -e DISCORD_BOT_TOKEN=... \
  -e MASTER_AGENT_BASE_IMAGE=codex-slack-v1-uat \
  -e MASTER_REGISTRY_PATH=/opt/codex-slack/data/master/agents.json \
  -e MASTER_DEFAULT_AGENT_ADAPTER=codex \
  -e MASTER_CODEX_COMMAND_TEMPLATE='codex exec --dangerously-bypass-approvals-and-sandbox resume --last -' \
  -e MASTER_CLAUDE_COMMAND_TEMPLATE='claude -p --dangerously-skip-permissions' \
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
If you prefer Compose, use the dedicated example file:
```bash
export UID="$(id -u)"
export GID="$(id -g)"
export PODMAN_SOCKET_PATH="/run/user/$(id -u)/podman/podman.sock"
export MASTER_DATA_DIR="$(pwd)/data/master"
export MASTER_RUNTIME_IMAGE="codex-slack-v1-uat"
export MASTER_CODEX_AUTH_JSON_PATH="${HOME}/.codex/auth.json"
export MASTER_CLAUDE_CONFIG_DIR_PATH="${HOME}/.claude"   # optional: share claude settings with agents
export MASTER_SSH_AUTH_SOCK_PATH="${SSH_AUTH_SOCK}"
export MASTER_ADMIN_CHANNELS="<admin_channel_id>"
export MASTER_AGENT_BASE_IMAGE="codex-slack-v1-uat"
export MASTER_GIT_USER_NAME="Your Name"
export MASTER_GIT_USER_EMAIL="you@example.com"
export CLAUDE_CODE_OAUTH_TOKEN="..."
export MASTER_FRONTENDS="slack,discord"
export DISCORD_BOT_TOKEN="..."
export DISCORD_ADMIN_CHANNELS="<discord_admin_channel_id>"
export MASTER_DEFAULT_AGENT_ADAPTER="codex"
export MASTER_CODEX_COMMAND_TEMPLATE='codex exec --dangerously-bypass-approvals-and-sandbox resume --last -'
export MASTER_CLAUDE_COMMAND_TEMPLATE='claude -p --dangerously-skip-permissions'
mkdir -p "${MASTER_DATA_DIR}"
podman compose -f docker-compose.master-agent.example.yml up --build -d
podman compose -f docker-compose.master-agent.example.yml logs -f
```
The compose example is intended for Podman Compose and already includes:
- `userns_mode: keep-id`
- `security_opt: [label=disable]`
- `x-podman.in_pod: false`

It also reads the master container image from `MASTER_RUNTIME_IMAGE` (default `codex-slack-v1-uat`) so you can swap tags without editing the compose file.

For non-container local debugging, you can also run:
```bash
python -m src.master.main
```

Important env notes:
- Set `MASTER_AGENT_BASE_IMAGE` to the image tag you actually rebuilt for agent containers. If unset, default-image agents still start from `codex-slack-bot:latest`.
- `MASTER_CODEX_AUTH_JSON_PATH` must be a host filesystem path visible to host Podman. It is mounted into each agent as `/run/secrets/codex_auth.json:ro`.
- `MASTER_SSH_AUTH_SOCK_PATH` must be a host filesystem path visible to host Podman. It is mounted into each agent as `/run/secrets/ssh-auth.sock`.
- The master container itself also needs the same SSH socket mounted separately, for example `-v /absolute/host/path/ssh-agent.sock:/ssh-agent`, with `SSH_AUTH_SOCK=/ssh-agent` so `/master-agent-load` can clone private repos over SSH.
- If `MASTER_SSH_KNOWN_HOSTS_PATH` is set, it must also be host-visible and is mounted into each agent as `/run/secrets/ssh_known_hosts:ro`.
- If `MASTER_SSH_KNOWN_HOSTS_PATH` is unset, master-side and agent-side SSH use `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null`.
- If `MASTER_GIT_USER_NAME` and `MASTER_GIT_USER_EMAIL` are set, the master passes them into each agent and the worker writes them into the checked-out repo's local Git config during startup.
- Default command template is `MASTER_AGENT_COMMAND_TEMPLATE='codex exec --dangerously-bypass-approvals-and-sandbox resume --last -'`.
- Default adapter is `MASTER_DEFAULT_AGENT_ADAPTER=codex`.
- If you use the `claude-code` adapter, rebuild the base image from this branch so the agent container includes the `claude` CLI binary.

Claude subscription auth in headless containers:
- Generate `CLAUDE_CODE_OAUTH_TOKEN` on the host with:
```bash
claude setup-token
```
- Export that token into the master container environment.
- If `CLAUDE_CODE_OAUTH_TOKEN` is present, the master prefers it over `ANTHROPIC_API_KEY` when building agent env.

Persistence notes:
- Without the `data/master` volume mount, `agents.json` is lost when the master container exits, so `/master-agent-list` will look empty after restart.
- The repo includes `docker-compose.master-agent.example.yml` as the baseline Compose definition for the master runtime.
2. Verify startup logs include:
- loaded admin channels
- registry path
- no token parsing errors
- no `podman CLI is not installed in the master runtime` errors before lifecycle commands
- no `unable to connect to Podman socket ... permission denied` errors after a lifecycle command

## Agent Lifecycle (Admin Channel)
1. Load mapping and image plan:
```text
/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]
```
2. Start agent:
```text
/master-agent-start <name>
```
3. Check status:
```text
/master-agent-status <name>
```
4. Stop/remove when done:
```text
/master-agent-stop <name>
/master-agent-remove <name>
```
5. Refresh the agent's persisted Codex auth after renewing the host auth file:
```text
/master-agent-refresh-auth <name>
```
6. Push updated global Claude config (CLAUDE.md / settings.json) to a running agent without restart:
```text
/master-agent-refresh-config <name>
```
This copies the current contents of `MASTER_CLAUDE_CONFIG_DIR_PATH` into the agent's workspace volume at `~/.claude/`. The agent picks up the new config on its next Claude invocation. No container restart required.
7. Override the claude model for a specific agent (persisted in registry, no restart needed):
```text
/master-agent-set-model <name> claude-opus-4-5
```
Omit the model argument to clear the override:
```text
/master-agent-set-model <name>
```

## Routing Validation
1. In mapped non-admin channel, mention bot with prompt.
2. Confirm master logs routing event for mapped agent.
3. Reply in the same thread without mention.
4. Confirm thread follow-up routes to same agent.

## Failure Recovery
### Build or start failure
- Use `/master-agent-status <name>` to inspect state.
- Check master logs for `ERR_RUNTIME_FAILED`.
- Check agent logs and Podman inspect data.
- If logs show `unable to connect to Podman socket ... permission denied`, switch to the rootless socket mount and confirm the container was started with `--userns=keep-id --security-opt label=disable`.
- Fix repo image config and retry `/master-agent-start <name>`.

### Channel mapping conflict
- If `ERR_CHANNEL_CONFLICT`, remove old owner mapping first:
```text
/master-agent-remove <old_agent>
```
- Re-run `/master-agent-load` for the new mapping.

### Worker init failure
- Inspect status file in agent container path:
`/tmp/master-agent/status.json`
- Check stage failure (`preflight`, `repo_sync`, `workspace_prepare`).
- If `preflight` shows `missing auth source: SSH_AUTH_SOCK or GH token`, ensure `GH_TOKEN` is set on the master container so it is passed into the agent.
- For SSH-based Git operations, ensure `MASTER_SSH_AUTH_SOCK_PATH` points to a live host SSH agent socket before recreating the agent.
- Validate that the mounted SSH auth path is a real Unix socket, not a regular file:
```bash
test -S "$MASTER_SSH_AUTH_SOCK_PATH" && echo OK || echo BAD
ls -l "$MASTER_SSH_AUTH_SOCK_PATH"
```
- If an exited agent shows `/run/secrets/ssh-auth.sock` as `-rwx...` (regular file) or `ssh-add -l` returns `Connection refused`, the mount source is stale/invalid. Re-export `MASTER_SSH_AUTH_SOCK_PATH` from a live `SSH_AUTH_SOCK`, restart master, then recreate the agent.
- If you want explicit host verification, also set `MASTER_SSH_KNOWN_HOSTS_PATH`; otherwise the default is to accept all hosts.
- Fix env/auth and restart agent.
- If project image Dockerfile uses `USER root` for package install, switch back to `USER appuser` at the end to preserve expected runtime identity and permissions.

### Codex refresh-token failure
- If the agent returns `Your refresh token has already been used to generate a new access token`, refresh the host auth source first (for example, `codex login` on the host that owns `MASTER_CODEX_AUTH_JSON_PATH`).
- Then run:
```text
/master-agent-refresh-auth <name>
```
- This updates `/workspace/home/.codex/auth.json` in the agent home and preserves existing `.codex` session state files.
- You do not need to remove the agent container for this recovery path.

### Rate-limited commands
- Master returns `ERR_RATE_LIMITED`.
- Wait for rate window or tune:
- `MASTER_COMMAND_RATE_LIMIT_COUNT`
- `MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS`

## Operational Notes
- Registry source of truth: `data/master/agents.json` (persist by mounting host `data/master` into `/opt/codex-slack/data/master` in the master container)
- Registry lock file: `data/master/agents.json.lock`
- Command/audit signals are emitted in master logs under `master.audit`.
- Removing an agent (`/master-agent-remove`) removes container/registry mapping, but does not delete named workspace volume `agent-workspace-<name>`.
- To remove workspace data explicitly:
```bash
podman volume rm agent-workspace-<name>
```
