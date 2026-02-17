# Container Runtime Guide

This project includes a containerized runtime for the Slack bot.

## Included Tools
The image ships with:
- Python 3.11 runtime
- `codex` CLI (installed via npm package `@openai/codex`)
- `git`, `gh`, `curl`, `jq`, `make`, `openssh-client`

## Required Mounts
The provided `docker-compose.yml` mounts:
- Workspace: `./:/workspace`
- Slack/env config: `./.env:/workspace/.env` (via `env_file`)
- Bot logs: `./logs:/workspace/logs`
- Codex config/auth: `${HOME}/.codex:/home/appuser/.codex`
- GitHub CLI auth: `${HOME}/.config/gh:/home/appuser/.config/gh`
- Git identity: `${HOME}/.gitconfig:/home/appuser/.gitconfig:ro`
- SSH keys: `${HOME}/.ssh:/home/appuser/.ssh:ro`

## Session Management
- Set `CODEX_SESSION_ID` to resume a specific Codex session.
- If `CODEX_SESSION_ID` is omitted, the bot generates an `auto-*` session ID and uses `CODEX_COMMAND_TEMPLATE_NO_SESSION` (default: `codex exec -`).

## Start
```bash
docker compose up --build
```

## Environment Variables
Minimum `.env` values:
```dotenv
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_ALLOWED_CHANNELS=C01234567
CODEX_WORKSPACE_PATH=/workspace
BOT_LOG_FILE=/workspace/logs/bot.log

# optional session behavior
CODEX_SESSION_ID=
CODEX_COMMAND_TEMPLATE=codex exec resume {session_id} -
CODEX_COMMAND_TEMPLATE_NO_SESSION=codex exec -

# timeout control (empty or <=0 disables timeout)
CODEX_TIMEOUT_SECONDS=
```
