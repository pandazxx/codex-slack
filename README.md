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

# 2. Configure secrets
cat > .env <<'EOF'
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

`docker compose up` starts `master` and `mosquitto`. Agent containers are spawned by master when you create a workspace in the UI.

For rootless Podman, set `CONTAINER_SOCKET_PATH=/run/user/$(id -u)/podman/podman.sock` and the `DOCKER_GID` of the socket owner. Full setup details live in [`docs/manuals/ops-manual.md`](docs/manuals/ops-manual.md).

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
