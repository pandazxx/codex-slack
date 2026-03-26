# Codex Slack Bridge

A Python service that connects a local Codex or Claude Code session to Slack and Discord, and optionally orchestrates multiple containerised AI agent workers from a single master process.

Built for teams that want AI coding assistance directly inside their chat workspace, without exposing credentials or running cloud-side inference proxies.

## Vision

The project is evolving from a single-session Slack bridge into a full multi-agent orchestration platform. The master runtime will manage any number of agent containers — each targeting a different repository and channel — from a single control plane reachable via Slack or Discord admin commands. Structured Claude Code (`.claude/`) and Codex (`AGENTS.md`, `.agents/skills/`) workflows govern how features are designed, built, tested, reviewed, and documented within this repository itself.

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

Full setup and runtime guides live under `docs/`; use `docs/manuals/ops-manual.md` as the primary entry point.
For Codex contributors, repository workflow instructions live in `AGENTS.md` and repo-local skills live under `.agents/skills/`.

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
│   ├── manuals/                  #   User and operator entry points
│   ├── guides/                   #   Setup guides, tutorials, and runbooks
│   ├── references/               #   Commands, config, logging, schemas
│   ├── design/                   #   Design and planning artifacts
│   ├── decisions/                #   Architecture Decision Records
│   ├── test-plans/               #   Acceptance plans and UAT checklists
│   ├── releases/                 #   Release notes
│   └── knowledge-base/           #   FAQ and lessons learned
├── .agents/                      # Codex repo-local workflow skills
│   └── skills/                   #   Workflow, role, and git helper skills
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
├── BUILD.md                      # Compatibility pointer to setup and ops docs
└── USAGE.md                      # Compatibility pointer to usage docs
```

## Further reading

- `docs/README.md` — documentation map and category index
- `docs/manuals/ops-manual.md` — setup, deployment, and operations entry point
- `docs/manuals/user-manual.md` — day-to-day usage entry point
- `docs/guides/slack-setup.md` — detailed Slack app configuration
- `docs/guides/discord-setup.md` — Discord app setup for master mode
- `docs/guides/container-runtime.md` — container runtime, Podman socket, and mounts
- `docs/guides/runbooks/master-agent.md` — master-agent operational runbook
- `docs/guides/runbooks/cd-daemon.md` — CD daemon operational runbook
- `docs/guides/tutorials.md` — step-by-step tutorials and checklists
- `docs/references/api.md` — implemented command surface
- `docs/references/config.md` — configuration keys and defaults
