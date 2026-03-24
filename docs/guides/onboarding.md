# Onboarding Guide

Getting started as a new contributor to this project.

## Prerequisites

- Python 3.11+
- Docker or Podman
- `codex-cli` or `claude` CLI installed and authenticated
- A Slack workspace where you can create apps, or a Discord server where you can register a bot

## First steps

1. Read `README.md` for a project overview.
2. Read `BUILD.md` to set up a local bot session.
3. Read `USAGE.md` to understand day-to-day operation.
4. Run the test suite: `pytest -q`

## Agent framework

This repository uses the Claude Code agent framework defined in `.claude/`. Before making significant changes:

1. Read `.claude/CLAUDE.md` for project-scope workflow rules.
2. Read `docs/knowledge-base/lessons-learned.md` for prior learnings.
3. Check `docs/decisions/` for any Architecture Decision Records relevant to your area.

The standard feature workflow (design → build → test → review → document → release) is defined in `.claude/CLAUDE.md` under *Common Workflow*.

## Running the test suite

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

## Key contacts and references

- Operational runbook: `docs/MASTER_AGENT_RUNBOOK.md`
- Slack app setup: `docs/SLACK_SETUP.md`
- Discord app setup: `docs/DISCORD_SETUP.md`
- Full doc map: `docs/DOCUMENTATION_INDEX.md`
