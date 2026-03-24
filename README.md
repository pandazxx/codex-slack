# Codex Slack Bridge

A Python service that connects a local Codex or Claude Code session to Slack and Discord, and optionally orchestrates multiple containerised AI agent workers from a single master process.

Built for teams that want AI coding assistance directly inside their chat workspace, without exposing credentials or running cloud-side inference proxies.

## Vision

The project is evolving from a single-session Slack bridge into a full multi-agent orchestration platform. The master runtime will manage any number of agent containers — each targeting a different repository and channel — from a single control plane reachable via Slack or Discord admin commands. A structured Claude Code agent framework (`.claude/`) governs how features are designed, built, tested, reviewed, and documented within this repository itself.

## What this project is not

- Not a hosted SaaS or cloud service — the bot and all agents run on infrastructure you control.
- Not a general-purpose LLM proxy — it bridges exactly one chat workspace to one or more local AI sessions.
- Not a replacement for your local Codex or Claude Code CLI — it wraps and exposes those tools; they must be installed and authenticated separately.
- Not a multi-tenant platform — access is restricted to allowlisted channel IDs and a fixed admin channel list.

## Bootstrap demo

Minimal path to get a single bot session running with Docker Compose:

```bash
# 1. Copy and fill in credentials
cp docker-compose.yml docker-compose.local.yml   # or edit in place

# 2. Set required environment variables
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
export SLACK_ALLOWED_CHANNELS=C01234567
export CODEX_SESSION_ID=<your-local-codex-session-id>

# 3. Build and start
docker compose up --build -d
docker compose logs -f
```

Verify in Slack: run `/codex-status` in an allowlisted channel, then send `@codex say hello`.

For Podman, add the override file:

```bash
export UID="$(id -u)" GID="$(id -g)"
podman compose -f docker-compose.yml -f docker-compose.podman.yml up --build -d
```

Full setup (Slack app creation, OAuth scopes, slash commands): `BUILD.md`.

## Project structure

```
.
├── src/                          # Application source code
│   ├── bot/                      #   Single-session Slack bot (attach mode)
│   ├── master/                   #   Master orchestration runtime (multi-agent)
│   ├── agent/                    #   Agent worker entrypoint and lifecycle
│   └── cd/                       #   CD daemon for automated deployments
├── tests/                        # Automated tests (mirrors src/ structure)
├── scripts/                      # Build, bootstrap, and utility scripts
├── config/                       # Environment and service configuration
├── docs/                         # All documentation
├── .claude/                      # Claude Code agent framework
│   ├── CLAUDE.md                 #   Project-scope agent instructions
│   ├── agents/                   #   Subagent definitions (architect, engineer, tester, reviewer, doc-writer)
│   └── commands/                 #   Slash-command skills (commit, pr, tag)
├── .github/workflows/            # CI/CD pipelines (image publish)
├── Dockerfile                    # Standard bot/master image
├── Dockerfile.agent-minimal      # Lean agent worker image
├── docker-compose.yml            # Single-bot Compose baseline
├── docker-compose.master-agent.example.yml  # Master runtime Compose example
├── docker-compose.multi-agent.example.yml   # Multi-agent Compose example
├── docker-compose.podman.yml     # Podman rootless override
├── BUILD.md                      # Local setup and Slack app configuration
└── USAGE.md                      # Day-to-day operation and troubleshooting
```

## Further reading

- `BUILD.md` — local environment setup, Slack app OAuth, slash command registration
- `USAGE.md` — day-to-day operation, prompt workflow, master-mode quick path
- `docs/SLACK_SETUP.md` — detailed Slack app configuration
- `docs/DISCORD_SETUP.md` — Discord app setup for master mode
- `docs/CONTAINER.md` — containerised runtime, Podman socket, volume mounts
- `docs/LOGGING.md` — log destination and level configuration
- `docs/MASTER_AGENT_RUNBOOK.md` — master-agent operational runbook (v3.0)
- `docs/MULTI_AGENT_SETUP.md` — running multiple agents in the same workspace
- `docs/CD_DAEMON.md` — CD daemon design and operator guide
- `docs/TUTORIALS.md` — step-by-step tutorials and release checklists
- `docs/DOCUMENTATION_INDEX.md` — canonical doc map and implemented command reference
