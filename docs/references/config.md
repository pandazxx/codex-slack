# Configuration Reference

This page summarizes the configuration keys read by each runtime component.

## Master Service

The master service (`src/master/`) reads these environment variables on startup.

| Key | Required | Default | Purpose |
|-----|----------|---------|---------|
| `MASTER_DATA_DIR` | No | `/opt/codex-slack/data/master` | Host-visible directory where `master_data.db` (SQLite) is stored |
| `MASTER_DRY_RUN` | No | `false` | Disable side-effecting runtime actions (container spawn/stop) |
| `MASTER_PORT` | No | `8080` | HTTP port the master FastAPI app listens on |
| `MASTER_AGENT_BASE_IMAGE` | No | `codex-slack-master:latest` | Container image used when spawning agent containers |
| `MASTER_AGENT_NETWORK` | No | `codex-slack_internal` | Docker/Podman network that agent containers are attached to |
| `MQTT_HOST` | No | `mosquitto` | Hostname of the MQTT broker |
| `MQTT_PORT` | No | `1883` | Port of the MQTT broker |
| `CONTAINER_RUNTIME` | No | `podman` | Container runtime to use: `podman` or `docker` |
| `MASTER_GIT_USER_NAME` | No | unset | Git author name forwarded to agent containers |
| `MASTER_GIT_USER_EMAIL` | No | unset | Git author email forwarded to agent containers |
| `CLAUDE_CODE_OAUTH_TOKEN` | No | unset | Claude Code OAuth token forwarded to agent containers |
| `ANTHROPIC_API_KEY` | No | unset | Anthropic API key forwarded to agent containers |
| `OPENAI_API_KEY` | No | unset | OpenAI API key forwarded to agent containers |
| `GH_TOKEN` | No | unset | GitHub token forwarded to agent containers for repo access |
| `MASTER_CODEX_AUTH_JSON_PATH` | No | unset | Host path to the shared Codex `auth.json` mounted into agents |
| `MASTER_SSH_AUTH_SOCK_PATH` | No | unset | Host path to the SSH agent socket mounted into agent containers |
| `MASTER_SSH_KNOWN_HOSTS_PATH` | No | unset | Host path to `known_hosts` mounted into agent containers |

### Database

SQLite database file: `{MASTER_DATA_DIR}/master_data.db` (default `/opt/codex-slack/data/master/master_data.db`).

Tables: `workspaces`, `workspace_agents`, `topics`, `sessions`, `messages`.

Soft-delete columns: `workspaces.archived_at TEXT` and `topics.archived_at TEXT`. Active records have `archived_at IS NULL`.

## Agent Worker

Each agent container (`src/agent/`) reads these environment variables.

| Key | Required | Default | Purpose |
|-----|----------|---------|---------|
| `WORKSPACE_ID` | Yes | empty | The workspace UUID — used to subscribe to the correct MQTT topic namespace |
| `MQTT_HOST` | No | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | No | `1883` | MQTT broker port |
| `CODEX_WORKSPACE_PATH` | No | `/workspace` | Root path inside the agent container for the repo clone and worktrees |
| `AGENT_REPO_URL` | No | empty | Repository to clone on startup |
| `AGENT_REPO_REF` | No | `main` | Branch/ref to check out |
| `AGENT_REPO_DIR` | No | `repo` | Subdirectory name under `CODEX_WORKSPACE_PATH` for the primary clone |
| `AGENT_STATUS_FILE` | No | `/tmp/master-agent/status.json` | File path where the worker writes its stage/status for inspection |
| `CODEX_HOME` | No | `/home/appuser/.codex` | Writable directory for Codex state |
| `AGENT_READY_POLL_SECONDS` | No | `5` | Polling interval used only if `WORKSPACE_ID` is absent (fallback idle loop) |
| `AGENT_GIT_USER_NAME` | No | empty | Git author name written into the cloned repo's local config |
| `AGENT_GIT_USER_EMAIL` | No | empty | Git author email written into the cloned repo's local config |
| `SSH_AUTH_SOCK` | No | empty | Path to the SSH agent socket inside the container |
| `GH_TOKEN` / `GITHUB_TOKEN` | No | empty | GitHub token for repo access (satisfies preflight auth check) |
| `GH_TOKEN_FILE` | No | empty | Absolute path to a file containing the GitHub token |
| `CLAUDE_CODE_OAUTH_TOKEN` | No | unset | Passed through from master for Claude authentication |
| `ANTHROPIC_API_KEY` | No | unset | Passed through from master for Claude authentication |

### Volume — Claude session persistence

Each agent container mounts a named Docker/Podman volume for Claude session state:

- Volume name: `codex-claude-{workspace_id}`
- Mount point inside container: `/home/appuser/.claude`
- Created automatically on first `docker run` / `podman run`

This volume ensures `claude` session IDs persist across container restarts.

### LLM CLI invocations

The agent selects the CLI based on the `adapter` field in the MQTT prompt payload:

**claude-code adapter:**
```
claude --print --verbose --output-format stream-json --dangerously-skip-permissions [--resume <session_id>] <prompt>
```
`--resume` is omitted on the first turn in a topic (no session yet). `--verbose` is required for `stream-json` output to emit the `result` event that carries the new session ID.

Session expiry: if `--resume` fails with `No conversation found with session ID`, the agent automatically retries the same prompt without `--resume`.

**codex adapter:**
```
codex --full-auto -q <prompt>
```
Codex sessions are not explicitly resumed via flag; the `CODEX_HOME` directory preserves state.

## CD Daemon

The CD daemon (`src/cd/`) reads these environment variables.

| Key | Required | Default | Purpose |
|-----|----------|---------|---------|
| `CD_IMAGE` | Yes | — | Image repository to track (e.g. `ghcr.io/org/codex-slack-master`) |
| `CD_IMAGE_TAG` | No | `latest` | Tag to watch |
| `CD_CONTAINER_NAME` | No | `codex-slack-master` | Target container name on the host |
| `CD_COMPOSE_FILE` | No | `docker-compose.master-agent.example.yml` | Path inside the daemon container to the compose file |
| `CD_COMPOSE_SERVICE` | No | `codex-slack-master` | Compose service name |
| `CD_COMPOSE_BINARY` | No | `podman-compose` | Compose command |
| `CD_ENV_FILE` | No | unset | Path inside the daemon container to the env file passed to compose |
| `CD_STATE_FILE` | No | `data/cd/state.json` | Path inside the daemon container for persisted daemon state |
| `CD_POLL_INTERVAL_SECONDS` | No | `300` | Registry poll interval in seconds |
| `CD_HEALTH_CHECK_DELAY_SECONDS` | No | `30` | Post-deploy health check delay in seconds |
| `CD_ROLLBACK_ON_FAILURE` | No | `true` | Enable automatic rollback on failed deploy |
| `CD_DRY_RUN` | No | `false` | Disable side effects |
| `CD_NOTIFY_SLACK_WEBHOOK_URL` | No | unset | Slack incoming-webhook URL for deploy/rollback notifications |
| `CD_NOTIFY_DISCORD_WEBHOOK_URL` | No | unset | Discord incoming-webhook URL for deploy/rollback notifications |
