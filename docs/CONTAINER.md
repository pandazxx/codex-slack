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
- Codex auth cache file (as secret input): `${HOME}/.codex/auth.json:/run/secrets/codex_auth.json:ro`

Only the Codex auth cache file is mounted read-only; the rest of your host auth/config files are not required.
- Slack secrets are provided via environment variables.
- Codex authentication is provided by copying mounted auth cache into writable `CODEX_HOME` at startup.
- GitHub authentication is provided via `GH_TOKEN`.
- `CODEX_HOME` defaults to `/home/appuser/.codex` inside the container.

## Safe Forwarding of `auth.json`
Configured in compose (default):
- bind mount `~/.codex/auth.json` to `/run/secrets/codex_auth.json:ro`
- entrypoint copies it to `${CODEX_HOME}/auth.json` on startup
- avoids permission errors for Codex caches/skills writes under `CODEX_HOME`
- prevents direct writes back to host auth file
- token refresh updates still will not persist to host from container
- refresh token manually on host (`codex login`) and restart container when needed

## Refresh Codex Token (Container Workflow)
Because the mounted auth file is read-only, refreshing inside the container does not update your host token cache.

1. Stop the container:
```bash
docker compose down
```
2. Refresh auth on the host machine:
```bash
codex login
test -f ~/.codex/auth.json
```
3. Restart the container so entrypoint copies the updated token into `CODEX_HOME`:
```bash
docker compose up --build -d
```
4. Verify from inside the running container:
```bash
docker compose exec bot sh -lc 'test -f /home/appuser/.codex/auth.json && echo "codex auth present"'
```
5. Verify end-to-end from Slack with `/codex-status`.

If `/codex-status` still reports auth errors, repeat `codex login` on host and fully recreate the service:
```bash
docker compose down
docker compose up --build --force-recreate -d
```

## Session Management
- Set `CODEX_SESSION_ID` to resume a specific Codex session.
- If `CODEX_SESSION_ID` is omitted, the bot generates an `auto-*` session ID and uses `CODEX_COMMAND_TEMPLATE_NO_SESSION`.

## Start
```bash
docker compose up --build
```

## Get Required Tokens
### `GH_TOKEN` (optional, for `gh` usage)
1. Open `https://github.com/settings/tokens`.
2. Create a token (fine-grained recommended) with repository permissions you need.
3. Copy it and store it securely.

## Environment Variables
Export required variables in your shell before startup:
```bash
export SLACK_BOT_TOKEN='xoxb-...'
export SLACK_APP_TOKEN='xapp-...'
export SLACK_ALLOWED_CHANNELS='C01234567'
export GH_TOKEN='github_pat_...'
```

Prepare Codex auth on host (once):
```bash
codex login
test -f ~/.codex/auth.json
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
