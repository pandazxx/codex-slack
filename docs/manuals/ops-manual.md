# Operations Manual

This manual is the primary entry point for setting up, deploying, and operating the v3 codex-slack system.

## System Overview

codex-slack v3 is a self-hosted chat platform for LLM coding agents. There is no Slack or Discord dependency — the web UI is the only frontend.

**Runtime components:**

| Component | What it is | Port |
|-----------|-----------|------|
| **master** | FastAPI app: REST API + Vue 3 SPA + WebSocket hub + MQTT client | 8080 |
| **mosquitto** | MQTT broker (Eclipse Mosquitto 2) | 1883 (internal only) |
| **agent containers** | One container per workspace; runs the claude/codex CLI | none |

**Persistent storage:**

| Location | What is stored |
|----------|---------------|
| `master_data` Docker volume → `/opt/codex-slack/data/master/master_data.db` | SQLite: workspaces, topics, messages, sessions, agent configs |
| `codex-claude-{workspace_id}` Docker volume → `/home/appuser/.claude` | Per-workspace Claude Code session state |

## Prerequisites

- Docker (or Podman) with Compose support on the host
- `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` for Claude Code agents
- `GH_TOKEN` for private repository access
- SSH agent socket (optional) for SSH-based Git authentication

## Quick Start (Docker Compose)

```bash
# 1. Clone the repository
git clone https://github.com/<org>/codex-slack.git
cd codex-slack

# 2. Create an env file with your secrets
cat > .env <<EOF
ANTHROPIC_API_KEY=sk-ant-...
GH_TOKEN=ghp_...
MASTER_GIT_USER_NAME=Your Name
MASTER_GIT_USER_EMAIL=you@example.com
CONTAINER_RUNTIME=docker
CONTAINER_SOCKET_PATH=/var/run/docker.sock
EOF

# 3. Build and start
docker compose up --build -d

# 4. Open the UI
open http://localhost:8080
```

The compose file (`docker-compose.yml`) starts `master` and `mosquitto`. Agent containers are spawned by master when you create a workspace in the UI.

## Compose Configuration

Key environment variables and bind-mounts (see [`docs/references/config.md`](../references/config.md) for full reference):

| Env Var | Default | Notes |
|---------|---------|-------|
| `MASTER_AGENT_BASE_IMAGE` | `codex-slack-master:latest` | Image used for new agent containers |
| `CONTAINER_RUNTIME` | `docker` | Use `podman` for rootless Podman |
| `CONTAINER_SOCKET_PATH` | `/var/run/docker.sock` | Host path to the container socket |
| `MQTT_HOST` | `mosquitto` | Set automatically by compose network DNS |
| `MASTER_DRY_RUN` | `false` | Set to `true` to disable container spawning |

For rootless Podman, set `CONTAINER_SOCKET_PATH=/run/user/$(id -u)/podman/podman.sock` and add the `DOCKER_GID` of the socket owner.

## Day-to-Day Operations

### Creating a workspace

1. Open `http://master-host:8080`.
2. Click **New Workspace**, enter a name and repository URL.
3. Master clones the repo into the agent container and registers the workspace in SQLite.
4. Two default agents are created automatically: `claude` (claude-code adapter) and `codex` (codex adapter).

### Archiving (soft-deleting) a workspace

Use the **Archive** button in the workspace detail view. This:
- Sets `archived_at` in the `workspaces` table
- Cascades `archived_at` to all active topics in the workspace
- Stops and removes the agent container

Archived workspaces and their topics are viewable as read-only via `/archived` in the UI.

### Managing agent configurations

Add or remove named agents per workspace via the **Agents** section in the workspace detail view. Valid adapters: `claude-code`, `codex`. The `subagent` field (optional) injects `--agent <subagent>` into the claude-code CLI invocation.

### Monitoring

| What | Where |
|------|-------|
| Master logs | `docker logs codex-slack-master` |
| Agent logs | `docker logs codex-agent-{workspace_id}` |
| Health check | `curl http://master-host:8080/health` |
| DB schema | `curl http://master-host:8080/schema` |

### Version display

`GET /health` returns `{"status": "ok", "version": "<build-version>"}`. The `version` field is the value of the `APP_VERSION` environment variable baked into the image at CI build time.

**Production shows the RC string, not the release string.** The `promote-release.yml` workflow promotes a build to production by retagging the RC image (e.g. `:v4.0-rc3` → `:v4.0`) without rebuilding. Because the image is not rebuilt, its `APP_VERSION` env var still contains the RC string that was baked in at RC build time. A production container reporting `version: "v4.0-rc3"` is correct and expected — it is the build that passed UAT and was promoted. This is not a deployment error.

Startup logs follow the same convention: the first field of every `master.startup`, `agent.startup`, and `cd.daemon_start` log line is `version=<build-version>`.

To confirm the running build:

```bash
curl http://master-host:8080/health
# {"status":"ok","version":"v4.0-rc3"}
```

Local and non-tagged CI builds report `version: "dev"`, which is an unambiguous signal that the image was not produced by a tagged RC build.

## Data Backup

The entire state (workspaces, topics, messages, sessions) is in the SQLite file:

```bash
docker cp codex-slack-master:/opt/codex-slack/data/master/master_data.db ./backup-$(date +%Y%m%d).db
```

Claude session state (resumes across container restarts) lives in named volumes `codex-claude-{workspace_id}`. Back these up with:

```bash
docker run --rm \
  -v codex-claude-<workspace_id>:/data:ro \
  -v $(pwd)/backup:/backup \
  alpine tar czf /backup/claude-sessions-<workspace_id>.tar.gz /data
```

## Related References

- [`docs/references/config.md`](../references/config.md) — full configuration key reference
- [`docs/references/api.md`](../references/api.md) — REST API, WebSocket, MQTT reference
- [`docs/guides/runbooks/master-agent.md`](../guides/runbooks/master-agent.md) — operational runbook for production operation
- [`docs/guides/runbooks/cd-daemon.md`](../guides/runbooks/cd-daemon.md) — automated deployment (CD daemon)
- `docs/releases/` — release-specific changes and migration notes
