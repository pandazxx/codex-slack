# Onboarding Guide

Getting started as a new contributor to the codex-slack v3 project.

## Prerequisites

- Python 3.11+
- Docker or Podman (with Compose support)
- Node.js 18+ and npm (for building the frontend)
- `claude` CLI (Claude Code) or `codex` CLI installed and authenticated on the host
- Basic familiarity with FastAPI and Vue 3

## What this project is

codex-slack v3 is a self-hosted chat interface for LLM coding agents. The system is structured around:

- **Master service** — a FastAPI app (`src/master/`) served on port 8080. Hosts the REST API, serves the Vue 3 SPA, runs a WebSocket hub for real-time agent output, and bridges MQTT messages from agents to browser clients.
- **Agent containers** — one Docker/Podman container per workspace. Runs `src/agent/mqtt_loop.py`, which subscribes to MQTT prompts and invokes `claude` or `codex` CLI.
- **Mosquitto** — MQTT broker that decouples master and agent containers.
- **SQLite** — the master's durable store (`master_data.db`). Five tables: `workspaces`, `workspace_agents`, `topics`, `sessions`, `messages`.

There are no Slack or Discord integrations in v3. The web UI is the only frontend.

## First steps

1. Read [`README.md`](../../README.md) for a project overview.
2. Read [`docs/decisions/0005-v3-system-architecture.md`](../decisions/0005-v3-system-architecture.md) to understand the v3 architecture decisions.
3. Read [`docs/manuals/ops-manual.md`](../manuals/ops-manual.md) to understand setup and deployment.
4. Read [`docs/references/api.md`](../references/api.md) for the REST API, WebSocket, and MQTT reference.
5. Run the test suite: `pytest -q`

## Agent framework

This repository uses the Claude Code framework in `.claude/`. Before making significant changes:

1. Read `.claude/CLAUDE.md` for project-scope workflow rules.
2. Read [`docs/knowledge-base/lessons-learned.md`](../knowledge-base/lessons-learned.md) for prior learnings.
3. Check `docs/decisions/` for Architecture Decision Records relevant to your area.

The standard feature workflow (design → build → test → review → document → release) is defined in `.claude/CLAUDE.md` under *Common Workflow*.

## Running locally with Docker Compose

```bash
# Copy and configure environment
cp .env.example .env   # edit ANTHROPIC_API_KEY, GH_TOKEN, etc.

# Build and start (builds frontend inside the Dockerfile)
docker compose up --build

# Open the UI
open http://localhost:8080
```

The compose stack starts `master` (port 8080) and `mosquitto` (internal only). Agent containers are spawned by master on workspace creation.

## Running the test suite

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

## Building the frontend standalone

```bash
cd frontend
npm ci
npm run build   # outputs to frontend/dist/, copied into src/master/static/ during Docker build
```

## Key references

- Documentation map: [`docs/README.md`](../README.md)
- API reference: [`docs/references/api.md`](../references/api.md)
- Configuration reference: [`docs/references/config.md`](../references/config.md)
- Operations manual: [`docs/manuals/ops-manual.md`](../manuals/ops-manual.md)
- Operational runbook: [`docs/guides/runbooks/master-agent.md`](runbooks/master-agent.md)
