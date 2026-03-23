# Multi-Agent Setup (Same Slack Workspace)

> **Historical reference.** This document describes the manual multi-agent setup approach predating the master-agent system. For current multi-agent deployments use the master-agent commands (`/master-agent-load`, `/master-agent-start`, etc.) documented in `docs/MASTER_AGENT_RUNBOOK.md`.

This guide runs multiple Codex agents in separate containers, each mapped to a different repository, while sharing one Slack workspace.

## Architecture
- One container per project/repo.
- One Slack app per agent (recommended).
- One dedicated Slack channel per agent.
- One `SLACK_ALLOWED_CHANNELS` value per container (single channel ID).

Example mapping:
- `codex-agent-foo` -> repo `/repos/foo` -> channel `#codex-foo`
- `codex-agent-bar` -> repo `/repos/bar` -> channel `#codex-bar`
- `codex-agent-baz` -> repo `/repos/baz` -> channel `#codex-baz`

Important:
- The bot application code is inside the image under `/opt/codex-slack`.
- Target project repos are mounted to `/workspace` (safe with the current image layout).
- `CODEX_WORKSPACE_PATH` should point to the mounted target repo path (`/workspace` in the example).

## Why one Slack app per agent
- Strong token isolation.
- No cross-channel reply confusion.
- Easier incident handling and token rotation.

## Slack Setup (Per Agent)
1. Create a new Slack app (for example `codex-foo-bot`).
2. Enable Socket Mode.
3. Add bot scopes:
   - `app_mentions:read`
   - `channels:history`
   - `chat:write`
   - `commands`
4. Enable event subscription:
   - `app_mention`
5. Add slash commands:
   - `/codex-status`
   - `/codex-attach`
   - `/codex-detach`
   - `/codex-conv-cancel`
   - `/codex-help`
6. Install app to workspace and capture:
   - `SLACK_BOT_TOKEN` (`xoxb-...`)
   - `SLACK_APP_TOKEN` (`xapp-...`)
7. Invite the bot only to its channel.

## Channel Routing
- Create one channel per project (`#codex-foo`, `#codex-bar`, `#codex-baz`).
- Set each container `SLACK_ALLOWED_CHANNELS` to exactly one channel ID (`C...`).
- Do not reuse the same allowlist channel for multiple agents.

## Run with Compose
Use `docker-compose.multi-agent.example.yml` and set environment variables in your shell (or `.env`).

The example already consolidates:
- Podman Linux fix: `x-podman.in_pod: false`
- Host UID/GID write-safe container execution: `userns_mode: keep-id` + `user: ${UID}:${GID}`
- SSH agent forwarding for `git`/`gh` over SSH (`SSH_AUTH_SOCK` + `known_hosts` mounts)

Prerequisites:
```bash
export UID="$(id -u)"
export GID="$(id -g)"
export SSH_AUTH_SOCK="${SSH_AUTH_SOCK:?start ssh-agent first}"
ssh-add -l
```

```bash
docker compose -f docker-compose.multi-agent.example.yml up -d --build
```

For Podman:
```bash
podman compose -f docker-compose.multi-agent.example.yml up -d --build
```

## Validation Checklist
1. `/codex-status` in each channel shows the expected session ID.
2. Mention `@bot` in `#codex-foo`; only foo agent replies.
3. Mention `@bot` in `#codex-bar`; only bar agent replies.
4. Logs are written under each repo `logs/` directory.
5. Restart one container and verify others are unaffected.

## Security Notes
- Prefer separate GitHub tokens per agent (`GH_TOKEN`) with least privilege.
- Mount `~/.codex/auth.json` and `~/.codex/sessions` read-only.
- Keep per-repo `.codex/` directories in `.gitignore`.
