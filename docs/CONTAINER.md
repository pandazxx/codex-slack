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

## Session Management
- Set `CODEX_SESSION_ID` to resume a specific Codex session.
- If `CODEX_SESSION_ID` is omitted, the bot generates an `auto-*` session ID and uses `CODEX_COMMAND_TEMPLATE_NO_SESSION`.

## Start
```bash
docker compose up --build
```

### Podman on Linux
If `/workspace` appears as `root:root` and writes fail for `appuser`, run with the Podman override so host UID/GID are preserved:
```bash
export UID="$(id -u)"
export GID="$(id -g)"
podman compose -f docker-compose.yml -f docker-compose.podman.yml up --build
```

The override enables:
- `user: ${UID}:${GID}` to run with host numeric IDs.
- `userns_mode: keep-id` so container UID/GID maps to the same host UID/GID.
- `x-podman.in_pod: false` so Podman Compose does not place the service in a pod (required to avoid `--userns and --pod cannot be set together`).
- `:Z` volume labels for SELinux-compatible bind mounts.

Note:
- `:U` is intentionally not used; it can chown files on the host mount and caused the ownership drift you saw.
- If you prefer global config, set Podman Compose to run without pods by default and keep `userns=keep-id` enabled for compose workloads.

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
export GIT_USER_NAME='Your Name'
export GIT_USER_EMAIL='you@example.com'
```

If `GIT_USER_NAME` / `GIT_USER_EMAIL` are set, entrypoint applies them via `git config --global` so commits work inside container.

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

## Git SSH Authentication (No Private Key Mount)
Use SSH agent forwarding so the container can authenticate to GitHub without copying `~/.ssh/id_*` into the image or volume.

Host setup (current shell):
```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
ssh-add -l
```

Host setup (persist across new shells):
```bash
# ~/.bashrc or ~/.zshrc
if [ -z "${SSH_AUTH_SOCK:-}" ]; then
  eval "$(ssh-agent -s)" >/dev/null
fi
ssh-add -l >/dev/null 2>&1 || ssh-add ~/.ssh/id_ed25519
```

Quick checks on host:
```bash
echo "$SSH_AUTH_SOCK"
ssh-add -l
ssh -T git@github.com
```

Start container with SSH override:
```bash
docker compose -f docker-compose.yml -f docker-compose.ssh.yml up --build -d
```

For Podman:
```bash
export UID="$(id -u)"
export GID="$(id -g)"
podman compose -f docker-compose.yml -f docker-compose.podman.yml -f docker-compose.ssh.yml up --build -d
```

Verify from inside container:
```bash
ssh -T git@github.com
git remote -v
```

Notes:
- `docker-compose.ssh.yml` mounts only `SSH_AUTH_SOCK` and `known_hosts` as read-only.
- Private key material remains on host and stays managed by host `ssh-agent`.
- If agent/key changes, restart the compose stack to refresh socket mapping.
- If `${HOME}/.ssh/known_hosts` is missing, create it with `ssh-keyscan github.com >> ~/.ssh/known_hosts`.

Optional variables:
```bash
export CODEX_SESSION_ID='sess_...'
export CODEX_TIMEOUT_SECONDS=''
```

After changing exported variables, recreate the container so new values are applied:
```bash
docker compose up -d --force-recreate
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
- `CODEX_COMMAND_TEMPLATE=codex exec --dangerously-bypass-approvals-and-sandbox - resume {session_id}`
- `CODEX_COMMAND_TEMPLATE_NO_SESSION=codex exec --dangerously-bypass-approvals-and-sandbox -`

Podman note:
- keep `{session_id}` as a literal in compose template values.
- avoid `${VAR:-...{session_id}...}` interpolation forms for these two variables, because some Podman/compose setups misparse braces.

Use this only in trusted environments.
