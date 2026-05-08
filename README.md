# codex-slack

A self-hosted web platform for orchestrating LLM coding agents (Claude Code, Codex) against your own Git repositories. Each workspace maps to a repository and is served by a dedicated agent container; topics are independent chat threads that get their own git worktree and LLM session.

The project name reflects its v2 origin as a Slack/Discord bridge. v3 dropped chat-platform integration in favour of a Vue 3 SPA + REST API; the name was kept to avoid breaking image tags, volume names, and external references. See [`docs/decisions/0006-drop-slack-discord-integration.md`](docs/decisions/0006-drop-slack-discord-integration.md) for the rationale.

## What this project is not

- Not a hosted SaaS — the master and all agent containers run on infrastructure you control.
- Not a multi-tenant platform — there is no per-user authentication; access control is whatever you put in front of the master HTTP port.
- Not a replacement for the local `claude` or `codex` CLI — agent containers wrap and invoke those tools; they must be available inside the agent image and authenticated via env vars.
- No longer a Slack or Discord bridge — chat-platform adapters were removed in v3. If you need one, build it as an external service that calls the REST API.

## Architecture at a glance

| Component | What it is | Port |
|---|---|---|
| `master` | FastAPI app: REST API + Vue 3 SPA + WebSocket hub + MQTT client | 8080 |
| `mosquitto` | MQTT broker (Eclipse Mosquitto 2) | 1883 (internal only) |
| agent containers | One per workspace; runs `src/agent/mqtt_loop.py`, invokes `claude` / `codex` CLI | none |
| `cd` daemon (optional) | Polls a registry tag and redeploys master/agent images on change | none |

State lives in two places:
- `master_data` Docker volume (SQLite at `/opt/codex-slack/data/master/master_data.db`) — workspaces, topics, messages, sessions, agent configs.
- `codex-claude-{workspace_id}` Docker volumes — per-workspace Claude Code session state.

## Quick start

```bash
# 1. Clone
git clone https://github.com/<org>/codex-slack.git
cd codex-slack

# 2. Configure secrets — see "Critical environment variables" below
cat > .env <<EOF
ANTHROPIC_API_KEY=sk-ant-...
GH_TOKEN=ghp_...
MASTER_GIT_USER_NAME=Your Name
MASTER_GIT_USER_EMAIL=you@example.com
CONTAINER_RUNTIME=docker
CONTAINER_SOCKET_PATH=/var/run/docker.sock
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
EOF

# 3. Build and start
docker compose up --build -d

# 4. Open the UI
open http://localhost:8080
```

`docker compose up` starts `master` and `mosquitto`. Agent containers are spawned by master when you create a workspace in the UI.

## Critical environment variables

These must be set correctly before `docker compose up`. They go in a `.env` file in the repo root (Compose loads it automatically) or in your shell environment. Wrong values usually surface as "permission denied" on the container socket or auth failures from the Claude / Codex CLI inside the agent — neither of which is obvious from a quick scan of master logs.

### `DOCKER_GID` — host GID of the docker / podman socket

**Why:** the master container runs as a non-root user but needs to talk to the host container runtime (Docker or Podman) to spawn agent containers. The compose file adds the master container to GID `${DOCKER_GID:-999}` via `group_add`. If that GID does not match the group that owns the socket on your host, master gets `permission denied` on every spawn attempt — workspaces will be created in the database but their agents never come up.

**How to find the right value:**

```bash
# Standard Docker (root daemon)
stat -c '%g' /var/run/docker.sock

# Rootless Podman (per-user socket)
stat -c '%g' /run/user/$(id -u)/podman/podman.sock

# macOS Docker Desktop / remote DOCKER_HOST: GID 0 is fine
echo 0
```

The default fallback `999` is a Linux convention that is **not** correct on most real systems — explicitly set this even when it looks like it might be 999.

**Where to set:** `.env` file at the repo root, e.g. `DOCKER_GID=988`. The deployment-time value can also be set in the shell that runs `docker compose ...`.

### `CONTAINER_SOCKET_PATH` — host path to the runtime socket

**Why:** Compose bind-mounts this into the master container at `/run/container.sock`. Wrong path → master logs `docker connection refused` / `no such file` at startup and exits.

**How to find:**
- Docker Engine on Linux: `/var/run/docker.sock` (default)
- Rootless Podman: `/run/user/$(id -u)/podman/podman.sock`
- Remote Docker over SSH: leave the path as `/var/run/docker.sock` on the *target* host and set `DOCKER_HOST=ssh://user@host` instead.

**Where to set:** `.env`. Pair with the matching `DOCKER_GID` from the same socket.

### `ANTHROPIC_API_KEY` *or* `CLAUDE_CODE_OAUTH_TOKEN` — Claude credentials

**Why:** every agent container needs one of these; without them, `claude` exits immediately and the topic shows an agent error on the first message. `CLAUDE_CODE_OAUTH_TOKEN` is preferred when available (matches the headless model used by the agent image).

**How to find:**
- API key: from the [Anthropic console](https://console.anthropic.com/) → Settings → API Keys.
- OAuth token: run `claude login` on a host where Claude Code is installed, then read `~/.claude/.credentials.json`.

**Where to set:** `.env` — only one of the two is needed.

### `GH_TOKEN` — GitHub credential for repo clone

**Why:** master forwards this into agent containers so they can clone and push to private repositories over HTTPS. Without it, agents error during the `repo_sync` startup stage for any repo that isn't public.

**How to find:** GitHub → Settings → Developer settings → [Personal access tokens](https://github.com/settings/tokens). A fine-grained token with `contents: read/write` on the target repos is enough; classic tokens with `repo` scope also work.

**Where to set:** `.env`. If you prefer SSH-based clone, leave `GH_TOKEN` unset and set `MASTER_SSH_AUTH_SOCK_PATH` to your host's SSH agent socket instead.

### `MASTER_GIT_USER_NAME` / `MASTER_GIT_USER_EMAIL`

**Why:** agent containers commit on your behalf when an agent decides to push a fix. With these unset, `git commit` inside the agent fails with `Author identity unknown`.

**Where to set:** `.env`. Use the identity you want recorded as the commit author.

### Other notable optional vars

- `MASTER_PUBLIC_URL` — externally reachable base URL of the web UI; used by the optional notification webhooks to build deep links into topics.
- `MASTER_NOTIFY_DISCORD_WEBHOOK_URL`, `MASTER_NOTIFY_TELEGRAM_BOT_TOKEN` + `_CHAT_ID` — agent-reply notification destinations. (Discord here is a webhook destination, not a chat-platform frontend; that was removed in v3.)
- `CD_NOTIFY_SLACK_WEBHOOK_URL`, `CD_NOTIFY_DISCORD_WEBHOOK_URL` — same idea but for the optional CD daemon's deploy/rollback notifications.

For the full list with types and defaults, see [`docs/references/config.md`](docs/references/config.md). For Podman, SSH forwarding, and other runtime concerns, see [`docs/guides/container-runtime.md`](docs/guides/container-runtime.md).

## Project structure

```
.
├── src/                          # Application source
│   ├── master/                   #   FastAPI app, MQTT hub, container orchestration
│   ├── agent/                    #   Agent worker (MQTT loop, CLI adapter)
│   └── cd/                       #   CD daemon for image-tag-driven redeploys
├── frontend/                     # Vue 3 SPA (built into src/master/static/ during image build)
├── tests/                        # Automated tests (mirrors src/ structure)
├── scripts/                      # Bootstrap and utility scripts
├── config/                       # Mosquitto, claude-global, codex-global config baked into images
├── docs/                         # All documentation (see docs/README.md for the map)
│   ├── manuals/                  #   User and operator entry points
│   ├── guides/                   #   Setup guides, tutorials, runbooks
│   ├── references/               #   API, config, logging, schemas
│   ├── design/                   #   Design and planning artifacts
│   ├── decisions/                #   Architecture Decision Records
│   ├── test-plans/               #   UAT checklists
│   ├── releases/                 #   Release notes
│   └── knowledge-base/           #   FAQ and lessons learned
├── .claude/                      # Claude Code agent framework (CLAUDE.md, subagents, slash commands)
├── .agents/                      # Codex repo-local skills
├── .github/workflows/            # CI: image build, RC promotion, on-demand builds
├── Dockerfile                    # Master image (FastAPI + frontend)
├── Dockerfile.agent-minimal      # Lean base image for agent containers
├── Dockerfile.cd-daemon          # CD daemon image
├── Dockerfile.dev                # Dev image (bind-mount source, live reload)
├── Dockerfile.test               # Test runner image (CI pytest)
├── docker-compose.yml            # Master + mosquitto baseline
├── docker-compose.override.yml   # Dev override: bind-mount source, live reload
├── docker-compose.ssh.yml        # SSH agent forwarding override
├── docker-compose.ci.yml         # CI compose for the test image
├── docker-compose.cd-daemon.example.yml      # CD daemon example
├── docker-compose.master-agent.example.yml   # Master+agent example
├── docker-compose.multi-agent.example.yml    # Multi-agent example
├── BUILD.md                      # Compatibility pointer to the ops manual
└── USAGE.md                      # Compatibility pointer to the user manual
```

## Further reading

- [`docs/README.md`](docs/README.md) — documentation map
- [`docs/manuals/ops-manual.md`](docs/manuals/ops-manual.md) — setup, deployment, and operations
- [`docs/manuals/user-manual.md`](docs/manuals/user-manual.md) — day-to-day usage of the web UI
- [`docs/guides/onboarding.md`](docs/guides/onboarding.md) — contributor onboarding
- [`docs/guides/runbooks/master-agent.md`](docs/guides/runbooks/master-agent.md) — master/agent operational runbook
- [`docs/guides/runbooks/cd-daemon.md`](docs/guides/runbooks/cd-daemon.md) — CD daemon runbook
- [`docs/references/api.md`](docs/references/api.md) — REST API, WebSocket, and MQTT reference
- [`docs/references/config.md`](docs/references/config.md) — configuration keys and defaults
- [`docs/decisions/0005-v3-system-architecture.md`](docs/decisions/0005-v3-system-architecture.md) — v3 architecture ADR
- [`docs/decisions/0006-drop-slack-discord-integration.md`](docs/decisions/0006-drop-slack-discord-integration.md) — chat-platform removal ADR
