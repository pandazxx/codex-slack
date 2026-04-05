# Tutorials

## v3.0 Delivery Scope
This cycle introduces dual frontend and dual adapter support for master-agent runtime.

## Tutorial 1: Boot a Single Bot Session
1. Export required env vars: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_ALLOWED_CHANNELS`, `CODEX_SESSION_ID`.
2. Use this sample Compose baseline (`docker-compose.yml`):

```yaml
services:
  codex-bot:
    build: .
    image: codex-slack-bot:latest
    environment:
      SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN}
      SLACK_APP_TOKEN: ${SLACK_APP_TOKEN}
      SLACK_ALLOWED_CHANNELS: ${SLACK_ALLOWED_CHANNELS}
      CODEX_SESSION_ID: ${CODEX_SESSION_ID}
      GH_TOKEN: ${GH_TOKEN:-}
      GIT_USER_NAME: ${GIT_USER_NAME:-}
      GIT_USER_EMAIL: ${GIT_USER_EMAIL:-}
    volumes:
      - ./:/workspace
      - ./logs:/workspace/logs
      - ${HOME}/.codex/auth.json:/run/secrets/codex_auth.json:ro
      - ${HOME}/.codex/sessions:/run/secrets/codex_sessions:ro
```

3. For Podman, add override (`docker-compose.podman.yml`):

```yaml
services:
  codex-bot:
    user: "${UID}:${GID}"
    userns_mode: keep-id
    security_opt:
      - label=disable
    x-podman:
      in_pod: false
```

4. Start bot with Compose (containerized runtime):

```bash
docker compose up --build -d
docker compose logs -f
```

   For Podman:

```bash
export UID="$(id -u)"
export GID="$(id -g)"
podman compose -f docker-compose.yml -f docker-compose.podman.yml up --build -d
podman compose -f docker-compose.yml -f docker-compose.podman.yml logs -f
```

5. In Slack, mention the bot in an allowlisted channel.
6. Confirm response and run `/codex-status`.

## Tutorial 2: Master Agent Command Flow
1. Start master: `python -m src.master.main` with `MASTER_ADMIN_CHANNELS` configured.
2. Load an agent:
   - `/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]`
3. Start agent:
   - `/master-agent-start <name>`
4. In the mapped channel, mention the master bot with a prompt.
5. Validate runtime state:
   - `/master-agent-status <name>`
6. Optional:
   - Attach images in the thread; image `url_private` references are appended to the routed prompt.
   - Check usage counters with `/master-agent-usage <name>`.

## Tutorial 3: Safe Auth Refresh
Use `/master-agent-refresh-auth <name>` when rotating Codex auth.

Expected behavior:
- Updates `CODEX_HOME/auth.json` in the agent workspace.
- Preserves existing Codex session state files under `.codex`.

## Tutorial 4: Day-2 Troubleshooting
1. Check command output payload (`ok`, `code`, `message`, `data`).
2. Verify agent status and runtime inspection.
3. Tail container logs when needed.
4. Re-run auth refresh or restart agent if auth/runtime drift is detected.
5. For SSH clone failures:
   - Ensure `MASTER_SSH_AUTH_SOCK_PATH` points to a live socket (`test -S ...`).
   - In debug container, `/run/secrets/ssh-auth.sock` must be socket type (`s...`), not regular file (`-...`).
6. For custom Dockerfiles:
   - If you switch to `USER root` for package install, switch back to `USER appuser` before image end.
7. If you remove an agent and still need to clear workspace state:
   - Delete named volume manually: `podman volume rm agent-workspace-<name>`.

## Tutorial 5: Member Onboarding for Personal GitHub Projects
Use this flow after master runtime is already deployed and healthy.

1. Prepare repository access for the agent identity.
   - If your workspace uses SSH forwarding, ensure the forwarded SSH key has access to your repo (deploy key or machine user).
   - If token-based auth is used, ensure token owner can read/write your repo.
2. Apply safe repo policy before first agent run.
   - Protect `main`/`master`.
   - Prefer PR-only merges with required checks.
3. Make your project automation-ready.
   - Include build/test/lint commands in repo docs.
   - Ensure tests can run non-interactively.
4. Send mapping request to master admin.
   - Provide agent name, repo URL, target Slack channel ID, and default branch.
   - Example request payload:
     - name: `alice-api`
     - repo: `git@github.com:teammate-org/alice-api.git`
     - channel: `C_ALICE_WORK`
     - branch: `master`
5. After admin starts the agent, work only in your mapped Slack channel.
   - Ask for feature branch workflow and PR creation.
   - Keep one logical task per thread to retain clean session context.
6. If access/auth fails, ask admin to run diagnostics.
   - `/master-agent-status <name>`
   - `/master-agent-refresh-auth <name>` (non-destructive to Codex session state)

## Tutorial 6: Project-Specific Image While Preserving Base Agent Setup
Use this when your project needs extra OS packages, CLI tools, or language runtimes.

1. Use the published minimal base image from registry (default path).
   - Base image:
     - `ghcr.io/<owner>/codex-slack-agent-minimal:latest`
   - Tag guidance:
     - `latest` for testing against current default branch
     - `vX.Y-rcN` for release-candidate validation
     - `sha-<commit>` for immutable pinning
   - Reason: no local base-image bootstrap is required for normal customization flow.

2. Optional fallback for local-only development:
   - If registry access is unavailable, build/tag locally from this repository root:

```bash
cd /workspace/repo
podman build -t codex-slack-agent-minimal:latest -f Dockerfile.agent-minimal .
podman tag codex-slack-agent-minimal:latest localhost/codex-slack-agent-minimal:latest
```

3. Add project image files in your repo:
   - `.prj_assistant/image/Dockerfile`
4. Keep base agent behavior by extending, not replacing.
   - Start with:
     - `FROM ghcr.io/<owner>/codex-slack-agent-minimal:latest`
   - Local fallback:
     - `FROM localhost/codex-slack-agent-minimal:latest`
   - Install only project dependencies on top.
   - Do not override `ENTRYPOINT`, container mode env flow, or workspace mount assumptions unless required.
5. Example Dockerfile:

```dockerfile
FROM ghcr.io/<owner>/codex-slack-agent-minimal:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
    jq ripgrep && \
    rm -rf /var/lib/apt/lists/*
```

6. Build locally in project repo before asking admin to start the agent:

```bash
podman build -t local-<project>-agent -f .prj_assistant/image/Dockerfile .prj_assistant/image
```

7. Run a smoke test for base behavior compatibility:
   - Container starts successfully.
   - `codex --version` and `claude --version` both work in the image.
   - `python -m src.agent.main` is still runnable in image context.
   - Required project tools are present (`jq`, `rg`, language runtime, etc.).

8. Hand off to admin for framework usage:
   - Admin runs `/master-agent-load ...` and `/master-agent-start ...`.
   - Master auto-detects `.prj_assistant/image/Dockerfile` and builds `codex-agent-<name>:latest`.
   - Verify `/master-agent-status <name>` shows dockerfile-based image plan.

## Tutorial 7: Global Codex and Claude Defaults with Repo-Level Overrides
Use this when you want master to provide shared default agent configuration while still allowing individual repos to override it with `.codex/` or `.claude/`.

1. Prepare global default directories on the master host.
   - Example layout:

```text
/opt/codex-slack/config/codex/
  config.toml
  AGENTS.md

/opt/codex-slack/config/claude/
  settings.json
  hooks/
```

2. Export master env vars for those directories.

```bash
export MASTER_CODEX_CONFIG_DIR_PATH=/opt/codex-slack/config/codex
export MASTER_CLAUDE_CONFIG_DIR_PATH=/opt/codex-slack/config/claude
```

3. Start or restart master with those env vars applied.
   - Master mounts both directories read-only into each agent container.
   - Worker seeds them into writable agent locations during startup.

4. Understand the precedence model.
   - Codex:
     - master global defaults seed into agent runtime `CODEX_HOME`
     - repo `.codex/` overlays on top and wins for conflicting files
   - Claude:
     - master global defaults seed into agent home `~/.claude`
     - repo `.claude/` remains the project-level override source

5. Add repo-level overrides only where needed.
   - Example repo layout:

```text
my-project/
  .codex/
    config.toml
  .claude/
    settings.json
```

6. Expected runtime behavior.
   - If repo `.codex/config.toml` exists, it overrides the global Codex `config.toml`.
   - If repo has no `.codex/`, the global Codex defaults still apply.
   - Global Claude defaults always seed into agent home.
   - Repo `.claude/` provides project-specific Claude behavior without removing the global defaults.

7. Start the agent normally.
   - `/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]`
   - `/master-agent-start <name>`

8. Validate the effective config inside the running agent if needed.
   - Codex runtime config:
     - `podman exec -it agent-<name> sh -lc 'ls -la /workspace/home/.codex'`
   - Claude home config:
     - `podman exec -it agent-<name> sh -lc 'ls -la /workspace/home/.claude'`
   - Repo overrides:
     - `podman exec -it agent-<name> sh -lc 'ls -la /workspace/repo/.codex /workspace/repo/.claude 2>/dev/null || true'`

9. Keep the ownership model clear.
   - Master-owned directories:
     - `MASTER_CODEX_CONFIG_DIR_PATH`
     - `MASTER_CLAUDE_CONFIG_DIR_PATH`
   - Project-owned overrides:
     - repo `.codex/`
     - repo `.claude/`
   - Project repos should override only what they need, not duplicate the full global config tree.

## Release Wrap-Up Checklist (v3.0)
- [ ] Dual frontend flows validated (Slack + Discord).
- [ ] Dual adapter flows validated (`codex` + `claude-code`).
- [ ] README and USAGE reflect current command behavior.
- [ ] Tutorials validated against current `master`.
- [ ] Release candidate notes drafted.
