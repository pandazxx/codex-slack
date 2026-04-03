# Container Runtime Guide

This project includes a containerized runtime for the Slack bot.

This guide is operational. The canonical runtime contract for master-managed
agent containers lives in
`docs/design/agent-container-runtime-design.md`.

## Runtime Images
This repository currently has two container-image roles:

- `Dockerfile`
  - broader master/runtime image
  - includes master-oriented tools such as `podman`, `gh`, `jq`, and `make`
- `Dockerfile.agent-minimal`
  - published base image for agent containers
  - intended to be extended by project repos through `.prj_assistant/image/Dockerfile`

## Included Tools
The published minimal agent base image from `Dockerfile.agent-minimal` ships with:
- Python 3.11 runtime
- `codex` CLI (installed via npm package `@openai/codex`)
- `claude` CLI
- `git`
- `openssh-client`
- agent entrypoint and Python dependencies required by `src.agent.main`

It intentionally does not include master-only tooling like `podman`, `gh`, `jq`, or `make`.

For detailed master-agent operational steps, see
`docs/guides/runbooks/master-agent.md`.

## Required Mounts
The provided `docker-compose.yml` mounts:
- Workspace: `./:/workspace`
- Bot logs: `./logs:/workspace/logs`
- Codex auth cache file (as secret input): `${HOME}/.codex/auth.json:/run/secrets/codex_auth.json:ro`
- Codex sessions directory (as secret input): `${HOME}/.codex/sessions:/run/secrets/codex_sessions:ro`

Only the Codex auth + sessions paths are mounted read-only; the rest of your host auth/config files are not required.
- Slack secrets are provided via environment variables.
- Codex authentication is provided by copying mounted auth cache into writable `CODEX_HOME` at startup.
- Claude authentication is provided either by `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`.
- GitHub authentication is provided via `GH_TOKEN`.
- `CODEX_HOME` selection on startup:
  1. Use explicit `CODEX_HOME` env var if set.
  2. Else use `/workspace/home/.codex` for the master-managed agent runtime.
  3. Else fallback to `/home/appuser/.codex`.

## Safe Forwarding of `auth.json`
Configured in compose (default):
- bind mount `~/.codex/auth.json` to `/run/secrets/codex_auth.json:ro`
- bind mount `~/.codex/sessions` to `/run/secrets/codex_sessions:ro`
- entrypoint copies auth/sessions into `${CODEX_HOME}` only when missing
- avoids permission errors for Codex caches/skills writes under `CODEX_HOME`
- prevents direct writes back to host auth file
- prevents direct writes back to host session files
- token refresh updates still will not persist to host from container
- refresh token manually on host (`codex login`) and restart container when needed

## Session Management
- Set `CODEX_SESSION_ID` (from host session) to resume a specific Codex session.
- If `CODEX_SESSION_ID` is omitted, the bot generates an `auto-*` session ID and uses `CODEX_COMMAND_TEMPLATE_NO_SESSION`.

Example:
```bash
export CODEX_SESSION_ID='019c7460-8aad-7df3-a70c-947d5857373a'
```

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
test -d ~/.codex/sessions
```

## Project-Specific `CODEX_HOME` (Recommended)
If `./.codex` exists in your repository root, container startup uses it automatically as `CODEX_HOME`.

Bootstrap project-local Codex state from global host state:
```bash
mkdir -p .codex
cp ~/.codex/auth.json .codex/auth.json
cp -a ~/.codex/sessions .codex/sessions
cp ~/.codex/config.toml .codex/config.toml 2>/dev/null || true
chmod 700 .codex
chmod 600 .codex/auth.json
```

Use project-local Codex on host too:
```bash
export CODEX_HOME="$PWD/.codex"
codex resume
```

Do not commit project-local Codex state. Keep `.codex/` in `.gitignore`.

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

## Master-Agent Container Operations
When using the master-agent runtime, the normal agent container operations are:

```text
/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]
/master-agent-start <name>
/master-agent-status <name>
/master-agent-stop <name>
/master-agent-remove <name>
/master-agent-refresh-auth <name>
/master-agent-refresh-config <name>
```

Useful in-container checks after an agent starts:

```bash
podman exec -it agent-<name> sh -lc 'echo "$HOME" "$CODEX_HOME"'
podman exec -it agent-<name> sh -lc 'ls -la /workspace/home/.codex /workspace/home/.claude'
podman exec -it agent-<name> sh -lc 'ls -la /workspace/repo/.codex /workspace/repo/.claude 2>/dev/null || true'
```

For auth/config injection details, user-scope vs project-scope paths, and
request-manifest behavior, refer to
`docs/design/agent-container-runtime-design.md`.

## Published Base Image Contract
Use the published minimal base image when a project needs extra packages or CLIs but should keep the standard agent runtime contract.

- Published image:
  - `ghcr.io/<owner>/codex-slack-agent-minimal:<tag>`
- Recommended tag choices:
  - `latest` for default-branch testing
  - `vX.Y-rcN` or release tags for controlled rollout
  - `sha-<commit>` for immutable pinning
- Project customization path:
  - `.prj_assistant/image/Dockerfile`

Example project Dockerfile:

```dockerfile
FROM ghcr.io/<owner>/codex-slack-agent-minimal:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
    jq ripgrep && \
    rm -rf /var/lib/apt/lists/*
```

Keep these invariants when extending the base:
- preserve `/workspace`, `/workspace/repo`, and `/workspace/home`
- preserve `CODEX_CONTAINER_MODE=agent-worker`
- do not replace `docker/entrypoint.sh` behavior unless you are intentionally leaving the standard runtime contract

## Codex Sandbox Bypass
Container mode is configured to run Codex with:
- `--dangerously-bypass-approvals-and-sandbox`

This is applied through:
- `CODEX_COMMAND_TEMPLATE=codex exec --dangerously-bypass-approvals-and-sandbox resume {session_id} -`
- `CODEX_COMMAND_TEMPLATE_NO_SESSION=codex exec --dangerously-bypass-approvals-and-sandbox -`

Podman note:
- keep `{session_id}` as a literal in compose template values.
- avoid `${VAR:-...{session_id}...}` interpolation forms for these two variables, because some Podman/compose setups misparse braces.

Use this only in trusted environments.
