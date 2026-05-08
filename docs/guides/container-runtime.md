# Container Runtime Guide

Operational reference for the v3 container runtime: image roles, mounts, sockets, auth forwarding, and Podman specifics. The canonical runtime contract for master-managed agent containers lives in [`docs/design/agent-container-runtime-design.md`](../design/agent-container-runtime-design.md).

## Image roles

| Image | Purpose |
|---|---|
| `Dockerfile` | Master image. FastAPI app + Vue 3 SPA build + `podman` / `gh` / `jq` / `make` for orchestration. |
| `Dockerfile.agent-minimal` | Lean agent base image. Python 3.11, `claude` CLI, `codex` CLI, `git`, `openssh-client`, agent entrypoint. Published to GHCR for project-specific extension. |
| `Dockerfile.cd-daemon` | CD daemon image. Polls a registry tag and redeploys when the digest changes. |
| `Dockerfile.dev` | Dev image with bind-mounted source for live reload. Used by `docker-compose.override.yml`. |
| `Dockerfile.test` | Test runner image. Used by CI via `docker-compose.ci.yml`. |

The minimal agent image intentionally omits master-only tooling (`podman`, `gh`, `jq`, `make`).

## Required mounts

The standard `docker-compose.yml` mounts:

- `master_data` named volume → `/opt/codex-slack/data/master` — SQLite database, attachments.
- `${CONTAINER_SOCKET_PATH}` → container socket inside master so it can spawn agents.
- `mosquitto_data` named volume → mosquitto persistence.

Per-workspace agent containers add:

- `codex-claude-{workspace_id}` named volume → `/home/appuser/.claude` — Claude session state, persistent across restarts.

Auth and config flow into agents through environment variables, not host file mounts:

- `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` for Claude.
- `GH_TOKEN` for GitHub HTTPS clone/push.
- `MASTER_SSH_AUTH_SOCK_PATH` (host path) for SSH-based Git auth — master mounts the socket into each agent.

## Configuration env vars (most common)

| Var | Default | Purpose |
|---|---|---|
| `CONTAINER_RUNTIME` | `docker` | Set to `podman` for rootless Podman. |
| `CONTAINER_SOCKET_PATH` | `/var/run/docker.sock` | Host path master mounts to talk to the engine. |
| `MASTER_AGENT_BASE_IMAGE` | `codex-slack-master:latest` | Image used to spawn new agent containers. |
| `MQTT_HOST` | `mosquitto` | Service name on the compose network. |
| `MASTER_GIT_USER_NAME`, `MASTER_GIT_USER_EMAIL` | – | Git author identity used inside agent containers. |
| `MASTER_DRY_RUN` | `false` | Skip container spawning; useful for tests. |
| `DOCKER_GID` | `999` | Numeric GID of the socket owner; required when running as non-root and the socket is owned by a group other than the container's. |

See [`docs/references/config.md`](../references/config.md) for the complete list.

## Starting the stack

```bash
docker compose up --build -d
docker compose logs -f master
```

For Podman rootless:

```bash
export CONTAINER_RUNTIME=podman
export CONTAINER_SOCKET_PATH=/run/user/$(id -u)/podman/podman.sock
export DOCKER_GID="$(stat -c '%g' "$CONTAINER_SOCKET_PATH")"
podman compose up --build -d
```

## SSH agent forwarding (no private key inside the image)

Use SSH agent forwarding so the container authenticates to GitHub without copying any key material into the image or volume. Keys stay on the host, managed by `ssh-agent`.

Host setup:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
ssh-add -l
echo "$SSH_AUTH_SOCK"
```

Bring up the stack with the SSH override:

```bash
docker compose -f docker-compose.yml -f docker-compose.ssh.yml up --build -d
```

For Podman, layer all three files:

```bash
podman compose \
  -f docker-compose.yml \
  -f docker-compose.podman.yml \
  -f docker-compose.ssh.yml \
  up --build -d
```

Verify from inside the master container:

```bash
docker exec -it codex-slack-master ssh -T git@github.com
```

Notes:

- `docker-compose.ssh.yml` mounts only `SSH_AUTH_SOCK` and `~/.ssh/known_hosts` (read-only).
- If `~/.ssh/known_hosts` is missing, create it: `ssh-keyscan github.com >> ~/.ssh/known_hosts`.
- If you re-launch `ssh-agent` (new socket), restart the stack so the mount picks up the new socket path.

## Codex auth caching (when using the codex adapter)

If you use the `codex` adapter, the master mounts `~/.codex/auth.json` (host) into each agent at `/run/secrets/codex_auth.json:ro`. The agent entrypoint copies it into a writable `CODEX_HOME` (defaults to `/workspace/home/.codex`) so token-refresh writes do not propagate back to the host. Refresh on the host with `codex login` and restart agents to pick up new tokens.

## Sandbox bypass

Agent containers run Codex with `--dangerously-bypass-approvals-and-sandbox`, configured by:

- `CODEX_COMMAND_TEMPLATE=codex exec --dangerously-bypass-approvals-and-sandbox resume {session_id} -`
- `CODEX_COMMAND_TEMPLATE_NO_SESSION=codex exec --dangerously-bypass-approvals-and-sandbox -`

Use this only in trusted environments. The agent runs your repository's code with whatever credentials the master forwards.

Podman quirk: keep `{session_id}` as a literal in compose values; some Podman/compose versions misparse `${VAR:-...{session_id}...}` interpolation forms.

## Useful in-container checks

```bash
# Master state
docker logs codex-slack-master
docker exec codex-slack-master cat /opt/codex-slack/data/master/master_data.db | sqlite3 - .tables

# Agent state for a workspace
docker logs codex-agent-<workspace_id>
docker exec codex-agent-<workspace_id> cat /tmp/master-agent/status.json
docker exec codex-agent-<workspace_id> sh -lc 'ls -la /home/appuser/.claude'
```

For master/agent operational procedures, see [`docs/guides/runbooks/master-agent.md`](runbooks/master-agent.md). For project-specific image extension, see [`docs/guides/project-agent-image.md`](project-agent-image.md).
