# Onboarding Guide

Getting started as a new contributor to this project.

## Prerequisites

- Python 3.11+
- Docker or Podman
- `codex-cli` or `claude` CLI installed and authenticated
- A Slack workspace where you can create apps, or a Discord server where you can register a bot

## First steps

1. Read [`README.md`](../../README.md) for a project overview.
2. Read [`docs/manuals/ops-manual.md`](../manuals/ops-manual.md) to understand setup and deployment.
3. Read [`docs/manuals/user-manual.md`](../manuals/user-manual.md) to understand day-to-day operation.
4. Run the test suite: `pytest -q`

## Agent framework

This repository uses both the Claude Code framework in `.claude/` and the Codex repo-local skill framework in `.agents/skills/`. Before making significant changes:

1. Read `.claude/CLAUDE.md` for project-scope workflow rules.
2. Read `AGENTS.md` for the repo-level Codex workflow contract.
3. Read [`docs/knowledge-base/lessons-learned.md`](../knowledge-base/lessons-learned.md) for prior learnings.
4. Check `docs/decisions/` for any Architecture Decision Records relevant to your area.

The standard feature workflow (design → build → test → review → document → release) is defined in `.claude/CLAUDE.md` under *Common Workflow*.

## Running the test suite

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

## Key contacts and references

- Documentation map: [`docs/README.md`](../README.md)
- Operational runbook: [`docs/guides/runbooks/master-agent.md`](runbooks/master-agent.md)
- Slack app setup: [`docs/guides/slack-setup.md`](slack-setup.md)
- Discord app setup: [`docs/guides/discord-setup.md`](discord-setup.md)
- User manual: [`docs/manuals/user-manual.md`](../manuals/user-manual.md)
