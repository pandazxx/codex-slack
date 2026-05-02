# Master-Agent Runbook (v3)

## Scope

Operational guide for the v3 codex-slack stack:
- `master` container: FastAPI app, REST API, Vue 3 SPA, WebSocket hub, MQTT client
- `mosquitto` container: MQTT broker
- Agent containers: one per workspace, runs `src/agent/mqtt_loop.py`

See [`docs/test-plans/master-agent-uat.md`](../../test-plans/master-agent-uat.md) for user acceptance test cases.

## Prerequisites

- Docker (or Podman) with Compose on the host.
- For rootless Podman: mount `/run/user/<uid>/podman/podman.sock` and set `CONTAINER_RUNTIME=podman`.
- `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` for Claude Code agents.
- `GH_TOKEN` for private repository access (passed from master into agent containers).
- Optional: `MASTER_SSH_AUTH_SOCK_PATH` — host path to an SSH agent socket for SSH-based Git auth.
- Optional: `MASTER_SSH_KNOWN_HOSTS_PATH` — host path to `known_hosts`; if omitted, SSH defaults to `StrictHostKeyChecking=no`.
- Docker/Podman socket accessible to master so it can spawn and stop agent containers.

## Starting the Stack

```bash
# Standard Docker Compose
export ANTHROPIC_API_KEY="sk-ant-..."
export GH_TOKEN="ghp_..."
export MASTER_GIT_USER_NAME="Your Name"
export MASTER_GIT_USER_EMAIL="you@example.com"
export CONTAINER_SOCKET_PATH="/var/run/docker.sock"  # or podman socket path

docker compose up -d
docker compose logs -f
```

For rootless Podman, add:
```bash
export CONTAINER_RUNTIME=podman
export CONTAINER_SOCKET_PATH="/run/user/$(id -u)/podman/podman.sock"
export DOCKER_GID="$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo 999)"
```

## Master Startup Verification

Check that startup logs contain:
- `master.startup mqtt=mosquitto:1883 ...`
- `master.db_init path=...master_data.db`
- `master.mqtt_loop_start host=mosquitto port=1883`
- No `docker connection refused` or socket permission errors

If all workspaces with a registered `container_name` are respawned on startup, you will see `master.respawned container=codex-agent-<id>` for each.

## Workspace Lifecycle (via UI)

1. **Create workspace** — UI form: name + repo URL. Master spawns agent container `codex-agent-{workspace_id}`.
2. **Use topics** — create topics, send messages, receive agent responses in real-time via WebSocket.
3. **Archive workspace** — Archive button in UI. Soft-deletes the workspace and all its active topics, stops the agent container.

Agent container naming:
```
codex-agent-{workspace_id}
```

Claude session volume naming:
```
codex-claude-{workspace_id}  →  /home/appuser/.claude  (inside agent container)
```

## Agent Container Management

Inspect a running agent:
```bash
docker logs codex-agent-<workspace_id>
docker inspect codex-agent-<workspace_id>
```

Check agent status file:
```bash
docker exec codex-agent-<workspace_id> cat /tmp/master-agent/status.json
```

Stop an agent manually (e.g. for debugging):
```bash
docker stop codex-agent-<workspace_id>
docker rm codex-agent-<workspace_id>
```

Master will respawn the container on next startup if the workspace is not archived.

## Failure Recovery

### Agent container not starting

1. Check `docker logs codex-agent-<workspace_id>` for stage failure.
2. Common stage failures:
   - `preflight`: missing auth — ensure `GH_TOKEN` or SSH socket is set.
   - `repo_sync`: `AGENT_REPO_URL` is invalid or unauthenticated.
   - `workspace_prepare`: filesystem permission error.
3. Fix the env/auth and archive+recreate the workspace if needed (or manually remove the container and restart master to trigger respawn).

### Agent exits with `ssh-auth.sock` error

If the mounted SSH auth sock becomes stale (e.g. agent was restarted with a new `SSH_AUTH_SOCK`):
```bash
# Re-export from live SSH_AUTH_SOCK and restart master
export MASTER_SSH_AUTH_SOCK_PATH="$SSH_AUTH_SOCK"
docker compose restart master
```

### Claude session expired

The agent auto-retries without `--resume` when it receives `No conversation found with session ID`. No manual action needed; the new session ID is stored automatically.

### MQTT broker connectivity

If agents are not receiving prompts:
```bash
docker logs codex-slack-mosquitto
docker exec codex-slack-master python -c "
import paho.mqtt.client as mqtt
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.connect('mosquitto', 1883, 5)
print('MQTT OK')
"
```

### Database recovery

The SQLite database is in the `master_data` Docker volume. To inspect:
```bash
docker run --rm \
  -v codex-slack_master_data:/data:ro \
  -it python:3.11-slim \
  sqlite3 /data/master/master_data.db ".tables"
```

## Operational Notes

- Data source of truth: `master_data.db` in the `master_data` Docker volume.
- Removing a workspace volume `codex-claude-{workspace_id}` deletes Claude session history; do this only after archiving the workspace.
- MQTT broker runs on the `internal` Docker network; it is not exposed on the host.
- The WebSocket endpoint is `/ws/{topic_id}` (no `/api/` prefix).
- All REST API endpoints are under `/api/`.
