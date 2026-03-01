# Master-Agent Runbook (v1)

## Scope
Operational guide for the master->agent v1 stack:
- master in container (Slack + orchestration)
- agent worker containers (no direct Slack connection)
- Podman host socket control path

See `docs/MASTER_AGENT_UAT.md` for step-by-step user acceptance test cases.
Containerized UAT is required for v1 sign-off; functional Slack-only checks are not sufficient.

## Prerequisites
- Host Podman service socket mounted into master container.
- For rootless Podman, mount `/run/user/<uid>/podman/podman.sock` and run the master container with `--userns=keep-id --security-opt label=disable`.
- `podman` CLI installed inside the master image/container.
- Provide `GH_TOKEN` on the master container so it can be forwarded into agent workers for repo access.
- Provide `MASTER_CODEX_AUTH_JSON_PATH` as a host path to the shared Codex `auth.json`; v1 forwards only this auth file to agents, not Codex session directories.
- Provide `MASTER_SSH_AUTH_SOCK_PATH` as a host path to the SSH agent socket for private repo checkout and push over SSH.
- Optional: provide `MASTER_SSH_KNOWN_HOSTS_PATH` as a host path to `known_hosts` for explicit SSH host verification. If omitted, master and agents default to `StrictHostKeyChecking=no` with `/dev/null` known hosts.
- Slack app configured with command/event scopes.
- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `MASTER_ADMIN_CHANNELS` set.
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
  -e SSH_AUTH_SOCK=/ssh-agent \
  -e MASTER_GIT_USER_NAME='Your Name' \
  -e MASTER_GIT_USER_EMAIL='you@example.com' \
  -e MASTER_CODEX_AUTH_JSON_PATH=/absolute/host/path/auth.json \
  -e MASTER_SSH_AUTH_SOCK_PATH=/absolute/host/path/ssh-agent.sock \
  -e MASTER_SSH_KNOWN_HOSTS_PATH=/absolute/host/path/known_hosts \
  -e MASTER_ADMIN_CHANNELS=<admin_channel_id> \
  -e MASTER_AGENT_BASE_IMAGE=codex-slack-v1-uat \
  -e MASTER_REGISTRY_PATH=/opt/codex-slack/data/master/agents.json \
  -e MASTER_AGENT_COMMAND_TEMPLATE='codex exec -' \
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
For non-container local debugging, you can also run:
```bash
python -m src.master.main
```
Set `MASTER_AGENT_BASE_IMAGE` to the image tag you actually rebuilt for agent containers. If this is left unset, default-image agents still start from `codex-slack-bot:latest`.
`MASTER_CODEX_AUTH_JSON_PATH` must be a host filesystem path visible to host Podman because the master uses the host Podman socket. It is mounted into each agent as `/run/secrets/codex_auth.json:ro`.
`MASTER_SSH_AUTH_SOCK_PATH` must be a host filesystem path visible to host Podman. It is mounted into each agent as `/run/secrets/ssh-auth.sock`.
The master container itself also needs the same socket mounted separately (for example `-v /absolute/host/path/ssh-agent.sock:/ssh-agent`) with `SSH_AUTH_SOCK=/ssh-agent` so `/master-agent-load` can clone private repos over SSH.
If `MASTER_SSH_KNOWN_HOSTS_PATH` is set, it must also be a host filesystem path visible to host Podman and is mounted into each agent as `/run/secrets/ssh_known_hosts:ro`.
If `MASTER_SSH_KNOWN_HOSTS_PATH` is unset, master-side and agent-side SSH use `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null`.
If `MASTER_GIT_USER_NAME` and `MASTER_GIT_USER_EMAIL` are set, the master passes them into each agent and the worker writes them into the checked-out repo's local Git config during startup so commits do not require manual setup.
Without the `data/master` volume mount, `agents.json` is lost when the master container exits, so `/master-agent-list` will look empty after restart.
2. Verify startup logs include:
- loaded admin channels
- registry path
- no token parsing errors
- no `podman CLI is not installed in the master runtime` errors before lifecycle commands
- no `unable to connect to Podman socket ... permission denied` errors after a lifecycle command

## Agent Lifecycle (Admin Channel)
1. Load mapping and image plan:
```text
/master-agent-load <name> <repo_path> <channel_id>
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
- If you want explicit host verification, also set `MASTER_SSH_KNOWN_HOSTS_PATH`; otherwise the default is to accept all hosts.
- Fix env/auth and restart agent.

### Rate-limited commands
- Master returns `ERR_RATE_LIMITED`.
- Wait for rate window or tune:
- `MASTER_COMMAND_RATE_LIMIT_COUNT`
- `MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS`

## Operational Notes
- Registry source of truth: `data/master/agents.json` (persist by mounting host `data/master` into `/opt/codex-slack/data/master` in the master container)
- Registry lock file: `data/master/agents.json.lock`
- Command/audit signals are emitted in master logs under `master.audit`.
