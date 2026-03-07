# Tutorials

## v2.2 Housekeeping Scope
This cycle is documentation-first and bugfix-only. No new feature development is planned.

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
   - `/master-agent-load <name> <repo_path> <channel_id> [branch]`
3. Start agent:
   - `/master-agent-start <name>`
4. In the mapped channel, mention the master bot with a prompt.
5. Validate runtime state:
   - `/master-agent-status <name>`

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

1. Ensure base agent image exists locally before custom build.
   - From this repository root, build base image:

```bash
cd /workspace/repo
podman build -t codex-slack-bot:latest -f Dockerfile .
podman tag codex-slack-bot:latest localhost/codex-slack-bot:latest
```

   - Reason: if base image is missing locally, Podman/Docker tries to pull from a public registry and fails.

2. Add project image files in your repo:
   - `.prj_assistant/image/Dockerfile`
3. Keep base agent behavior by extending, not replacing.
   - Start with:
     - `FROM localhost/codex-slack-bot:latest`
     - or `FROM ghcr.io/<owner>/codex-slack-agent-minimal:latest` if your org uses the published minimal agent base.
   - Install only project dependencies on top.
   - Do not override `ENTRYPOINT`, container mode env flow, or workspace mount assumptions unless required.
4. Example Dockerfile:

```dockerfile
FROM localhost/codex-slack-bot:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
    jq ripgrep && \
    rm -rf /var/lib/apt/lists/*
```

5. Build locally in project repo before asking admin to start the agent:

```bash
podman build --pull-never -t local-<project>-agent -f .prj_assistant/image/Dockerfile .prj_assistant/image
```

6. Run a smoke test for base behavior compatibility:
   - Container starts successfully.
   - `python -m src.agent.main` is still runnable in image context.
   - Required project tools are present (`jq`, `rg`, language runtime, etc.).

7. Hand off to admin for framework usage:
   - Admin runs `/master-agent-load ...` and `/master-agent-start ...`.
   - Master auto-detects `.prj_assistant/image/Dockerfile` and builds `codex-agent-<name>:latest`.
   - Verify `/master-agent-status <name>` shows dockerfile-based image plan.

## Release Wrap-Up Checklist (v2.2)
- [ ] No net-new features merged.
- [ ] Bugfixes include tests.
- [ ] README and USAGE reflect current command behavior.
- [ ] Tutorials validated against current `master`.
- [ ] Release candidate notes drafted.
