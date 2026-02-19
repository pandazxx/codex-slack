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
- Bot logs: `./logs:/workspace/logs`

No host auth/config mounts are required.
- Slack secrets are provided via environment variables.
- Codex authentication is provided via `OPENAI_API_KEY`.
- GitHub authentication is provided via `GH_TOKEN`.
- `CODEX_HOME` defaults to `/home/appuser/.codex` inside the container.

## Session Management
- Set `CODEX_SESSION_ID` to resume a specific Codex session.
- If `CODEX_SESSION_ID` is omitted, the bot generates an `auto-*` session ID and uses `CODEX_COMMAND_TEMPLATE_NO_SESSION`.

## Start
```bash
docker compose up --build
```

## Get Required Tokens
### `OPENAI_API_KEY`
1. Open `https://platform.openai.com/api-keys`.
2. Create a new secret key.
3. Copy it and store it securely.

### `GH_TOKEN`
1. Open `https://github.com/settings/tokens`.
2. Create a token (fine-grained recommended) with repository permissions you need.
3. Copy it and store it securely.

## Environment Variables
Export required variables in your shell before startup:
```bash
export SLACK_BOT_TOKEN='xoxb-...'
export SLACK_APP_TOKEN='xapp-...'
export SLACK_ALLOWED_CHANNELS='C01234567'
export OPENAI_API_KEY='sk-...'
export GH_TOKEN='github_pat_...'
```

## Run Example
```bash
mkdir -p logs
docker compose up --build -d
docker compose logs -f
```

Optional variables:
```bash
export CODEX_SESSION_ID='sess_...'
export CODEX_TIMEOUT_SECONDS=''
```

## Verify
In your allowlisted Slack channel:
1. Run `/codex-status`.
2. Send `@codex hello`.
3. Confirm the bot replies and logs are written to `./logs/bot.log`.

## Codex Sandbox Bypass
Container mode is configured to run Codex with:
- `--dangerously-bypass-approvals-and-sandbox`

This is applied through:
- `CODEX_COMMAND_TEMPLATE=codex exec --dangerously-bypass-approvals-and-sandbox resume {session_id} -`
- `CODEX_COMMAND_TEMPLATE_NO_SESSION=codex exec --dangerously-bypass-approvals-and-sandbox -`

Use this only in trusted environments.
