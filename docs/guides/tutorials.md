# Tutorials

Step-by-step walkthroughs for the v3 stack. Each tutorial assumes the master container is reachable at `http://localhost:8080`; substitute your host where appropriate.

If you need a deeper reference, follow the link at the end of each tutorial.

## Tutorial 1: Bring up the stack

Goal: get `master` and `mosquitto` running locally with Docker Compose.

```bash
git clone https://github.com/<org>/codex-slack.git
cd codex-slack

cat > .env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
GH_TOKEN=ghp_...
MASTER_GIT_USER_NAME=Your Name
MASTER_GIT_USER_EMAIL=you@example.com
CONTAINER_RUNTIME=docker
CONTAINER_SOCKET_PATH=/var/run/docker.sock
EOF

docker compose up --build -d
docker compose logs -f master
```

Verify:

```bash
curl -s http://localhost:8080/health
# {"status":"ok","version":"dev"}
```

Open `http://localhost:8080` in a browser; you should see the empty workspace list.

Reference: [`docs/manuals/ops-manual.md`](../manuals/ops-manual.md).

## Tutorial 2: Create a workspace and send a message

Goal: clone a repo into an agent container and exchange messages with `claude`.

1. In the UI, click **New Workspace**.
2. Enter a display name and the HTTPS or SSH URL of a Git repository the agent can read with the credentials provided in `.env`.
3. Submit. Master clones the repo and starts an agent container `codex-agent-<workspace_id>`. Two default agents are registered: `claude` (claude-code adapter) and `codex` (codex adapter).
4. Open the workspace, click **New Topic**, give it a subject.
5. In the topic, type `@claude summarise the README` and submit. A thinking spinner appears; activity rows stream in (`⚙ Bash`, `📄 Read`, etc.) while the agent works; the final reply replaces them when done.

Reference: [`docs/manuals/user-manual.md`](../manuals/user-manual.md).

## Tutorial 3: Driving the system over HTTP

Goal: do everything tutorial 2 does, but via the REST API.

```bash
BASE=http://localhost:8080/api

# Create a workspace
curl -s -XPOST $BASE/workspaces \
  -H 'content-type: application/json' \
  -d '{"name":"my-repo","repo_url":"https://github.com/<owner>/<repo>.git"}'

# List workspaces
curl -s $BASE/workspaces

# Create a topic in workspace WID
curl -s -XPOST $BASE/workspaces/$WID/topics \
  -H 'content-type: application/json' \
  -d '{"subject":"hello"}'

# Post a message to topic TID
curl -s -XPOST $BASE/workspaces/$WID/topics/$TID/messages \
  -H 'content-type: application/json' \
  -d '{"body":"@claude what does this repo do?"}'
```

To stream agent output in real time, open a WebSocket to `/ws/events`. See [`docs/references/api.md`](../references/api.md) for the full surface.

## Tutorial 4: Resume sessions across restarts

Each agent maintains a separate Claude session per `(workspace, topic)` pair. Session state is stored both in the master's SQLite (`sessions` table) and in the per-workspace volume `codex-claude-{workspace_id}` mounted at `/home/appuser/.claude` inside the agent container. This means:

- A topic resumes its conversation automatically across master and agent restarts.
- If a session expires server-side, the agent transparently retries the prompt with a fresh session ID and persists the new ID.

To wipe a workspace's Claude history, archive the workspace, then delete the volume:

```bash
docker volume rm codex-claude-<workspace_id>
```

## Tutorial 5: Use a project-specific agent image

Most projects work with the default agent image. If your project needs extra tools (`jq`, `ripgrep`, language runtimes), provide a project-specific Dockerfile.

1. In your project repo, add `.prj_assistant/image/Dockerfile`:

   ```dockerfile
   FROM ghcr.io/<owner>/codex-slack-agent-minimal:latest

   RUN apt-get update && apt-get install -y --no-install-recommends \
       jq ripgrep && \
       rm -rf /var/lib/apt/lists/*
   ```

2. Push to the branch the workspace will track. On the next workspace-agent rebuild, master detects the Dockerfile and builds `codex-agent-<workspace>:latest` from it before starting the agent container.

3. Keep these invariants:
   - Do not change `WORKDIR` away from `/workspace`.
   - Do not replace `docker/entrypoint.sh`.
   - Do not switch the container `USER` away from `appuser` at image end.

Reference: [`docs/guides/project-agent-image.md`](project-agent-image.md).

## Tutorial 6: Share global Claude/Codex defaults

Mount per-host default config directories that all agent containers seed from at startup.

1. On the master host, lay out the defaults:

   ```
   /opt/codex-slack/config/codex/
     config.toml
     AGENTS.md

   /opt/codex-slack/config/claude/
     settings.json
     hooks/
   ```

2. Set master env vars (in `.env` or compose):

   ```bash
   MASTER_CODEX_CONFIG_DIR_PATH=/opt/codex-slack/config/codex
   MASTER_CLAUDE_CONFIG_DIR_PATH=/opt/codex-slack/config/claude
   ```

3. Restart master. Both directories are mounted read-only into each agent container and seeded into writable agent locations on startup.

4. Repos can override on top:
   - `repo/.codex/config.toml` overrides the global Codex `config.toml`.
   - `repo/.claude/` is treated as project-scope Claude config and layered on top of the global defaults.

Reference: [`docs/references/config.md`](../references/config.md).

## Tutorial 7: Day-2 troubleshooting

| Symptom | First check | Reference |
|---|---|---|
| Agent container won't start | `docker logs codex-agent-<wsid>` for stage failure (preflight, repo_sync, workspace_prepare) | [`docs/guides/runbooks/master-agent.md`](runbooks/master-agent.md) |
| SSH agent socket errors | Confirm `MASTER_SSH_AUTH_SOCK_PATH` points to a live socket on the host | [`docs/guides/runbooks/master-agent.md`](runbooks/master-agent.md) |
| Claude session expired | Self-healing — agent retries without `--resume` and stores the new session ID | [`docs/manuals/user-manual.md`](../manuals/user-manual.md) |
| MQTT not delivering prompts | Tail `mosquitto` logs; check that master logs `master.mqtt_loop_start host=mosquitto port=1883` | [`docs/guides/runbooks/master-agent.md`](runbooks/master-agent.md) |
| Production reports `version: vX.Y-rcN` | Expected — promotion retags the RC image without rebuilding | [`docs/manuals/ops-manual.md`](../manuals/ops-manual.md) |
