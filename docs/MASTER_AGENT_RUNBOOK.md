# Master-Agent Runbook (v1)

## Scope
Operational guide for the master->agent v1 stack:
- master in container (Slack + orchestration)
- agent worker containers (no direct Slack connection)
- Podman host socket control path

See `docs/MASTER_AGENT_UAT.md` for step-by-step user acceptance test cases.

## Prerequisites
- Host Podman service socket mounted into master container.
- Slack app configured with command/event scopes.
- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `MASTER_ADMIN_CHANNELS` set.
- Shared auth refs available to agents (`SSH_AUTH_SOCK` and/or `GH_TOKEN_FILE`).

## Master Startup
1. Start master runtime:
```bash
python -m src.master.main
```
2. Verify startup logs include:
- loaded admin channels
- registry path
- no token parsing errors

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
- Fix repo image config and retry `/master-agent-start <name>`.

### Channel mapping conflict
- If `ERR_CHANNEL_CONFLICT`, remove old owner mapping first:
```text
/master-agent-remove <old_agent>
```
- Re-run `/master-agent-load` for the new mapping.

### Worker init failure
- Inspect status file in agent container path:
`/run/master-agent/status.json`
- Check stage failure (`preflight`, `repo_sync`, `workspace_prepare`).
- Fix env/auth and restart agent.

### Rate-limited commands
- Master returns `ERR_RATE_LIMITED`.
- Wait for rate window or tune:
- `MASTER_COMMAND_RATE_LIMIT_COUNT`
- `MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS`

## Operational Notes
- Registry source of truth: `data/master/agents.json`
- Registry lock file: `data/master/agents.json.lock`
- Command/audit signals are emitted in master logs under `master.audit`.
