# Configuration Reference

This page summarizes the main configuration keys loaded directly from the current codebase.

## Single Bot Mode

| Key | Required | Default | Purpose |
| --- | --- | --- | --- |
| `SLACK_BOT_TOKEN` | Yes | None | Slack bot token for bot mode |
| `SLACK_APP_TOKEN` | Yes | None | Slack app token for Socket Mode |
| `SLACK_ALLOWED_CHANNELS` | Yes | None | Comma-separated allowlist for bot interaction |
| `CODEX_WORKSPACE_PATH` | Yes | None | Workspace path mounted into the runtime |
| `CODEX_COMMAND_TEMPLATE` | No | `codex exec resume {session_id} -` | Command used when a session id is known |
| `CODEX_COMMAND_TEMPLATE_NO_SESSION` | No | `codex exec -` | Command used when no session id is known |
| `CODEX_TIMEOUT_SECONDS` | No | unset | Dispatch timeout for bot mode |
| `CODEX_SESSION_ID` | No | unset | Session to resume on startup |
| `BOT_LOG_FILE` | No | unset | Optional file log destination |

## Master Mode

| Key | Required | Default | Purpose |
| --- | --- | --- | --- |
| `MASTER_FRONTENDS` | No | `slack` | Enabled frontends: `slack`, `discord`, or both |
| `SLACK_BOT_TOKEN` | Slack only | None | Slack bot token |
| `SLACK_APP_TOKEN` | Slack only | None | Slack app token |
| `MASTER_ADMIN_CHANNELS` | Slack only | None | Slack admin channels |
| `DISCORD_BOT_TOKEN` | Discord only | None | Discord bot token |
| `DISCORD_ADMIN_CHANNELS` | Discord only | None | Discord admin channels |
| `MASTER_REGISTRY_PATH` | No | `data/master/agents.json` | Container path inside the master container for the agent registry file |
| `MASTER_THREAD_STATE_PATH` | No | sibling of registry path | Container path inside the master container for thread tracking state |
| `MASTER_DRY_RUN` | No | `false` | Disable side-effecting runtime actions |
| `MASTER_AGENT_BASE_IMAGE` | No | `codex-slack-bot:latest` | Default image for new agents |
| `MASTER_CODEX_AUTH_JSON_PATH` | No | unset | Host path to the shared Codex auth file mounted into agents |
| `MASTER_CODEX_CONFIG_DIR_PATH` | No | unset or auto-detected from `MASTER_PROJECT_DIR` (`config/codex-global`, fallback `config/codex`) | Host path to the shared Codex config directory mounted into agents |
| `MASTER_CLAUDE_CONFIG_DIR_PATH` | No | unset or auto-detected from `MASTER_PROJECT_DIR` (`config/claude-global`) | Host path to the shared Claude config directory mounted into agents |
| `MASTER_PROJECT_DIR` | No | unset | Host project root used for global config auto-detection |
| `MASTER_SSH_AUTH_SOCK_PATH` | No | unset | Host path to the SSH agent socket mounted into master/agents |
| `MASTER_SSH_KNOWN_HOSTS_PATH` | No | unset | Host path to the SSH known-hosts file mounted into agents |
| `MASTER_GIT_USER_NAME` | No | unset | Git author name for agent workspaces |
| `MASTER_GIT_USER_EMAIL` | No | unset | Git author email for agent workspaces |
| `MASTER_AGENT_COMMAND_TEMPLATE` | No | `codex exec --dangerously-bypass-approvals-and-sandbox resume --last -` | Legacy default command template |
| `MASTER_CODEX_COMMAND_TEMPLATE` | No | inherited from `MASTER_AGENT_COMMAND_TEMPLATE` | Codex dispatch command |
| `MASTER_CLAUDE_COMMAND_TEMPLATE` | No | `claude -p --output-format json --dangerously-skip-permissions` | Claude dispatch command |
| `MASTER_DEFAULT_AGENT_ADAPTER` | No | `codex` | Default agent adapter |
| `MASTER_AGENT_TIMEOUT_SECONDS` | No | unset | Dispatch timeout |
| `MASTER_AGENT_AUTH_REFRESH_MAX_AGE_DAYS` | No | `2` | Maximum age, in days, for persisted Codex auth refresh state before routed Codex prompts refresh auth again |
| `MASTER_COMMAND_RATE_LIMIT_COUNT` | No | `20` | Per-user command burst limit |
| `MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS` | No | `60` | Rate-limit window size |

## Agent Worker Runtime

| Key | Required | Default | Purpose |
| --- | --- | --- | --- |
| `CODEX_WORKSPACE_PATH` | No | `/workspace` | Container path inside the agent for the workspace mount |
| `AGENT_REPO_URL` | No | empty | Repository to clone |
| `AGENT_REPO_REF` | No | `main` | Ref to check out |
| `AGENT_REPO_DIR` | No | `repo` | Checkout directory name |
| `AGENT_STATUS_FILE` | No | `/tmp/master-agent/status.json` | Container path inside the agent for the readiness status file |
| `CODEX_HOME` | No | `/home/appuser/.codex` | Container path inside the agent for the writable Codex home |
| `AGENT_READY_POLL_SECONDS` | No | `5` | Readiness polling interval |
| `AGENT_GLOBAL_CODEX_CONFIG_DIR` | No | empty | Container path inside the agent pointing at the mounted shared Codex config source |
| `AGENT_GLOBAL_CLAUDE_CONFIG_DIR` | No | empty | Container path inside the agent pointing at the mounted shared Claude config source |
| `AGENT_GIT_USER_NAME` | No | empty | Per-agent Git author name |
| `AGENT_GIT_USER_EMAIL` | No | empty | Per-agent Git author email |
| `SSH_AUTH_SOCK` | No | empty | Container path inside the agent to the passed-through SSH agent socket |
| `GH_TOKEN` / `GITHUB_TOKEN` | No | empty | Token value, not a path |
| `GH_TOKEN_FILE` | No | empty | Container path inside the agent to a token file for repo access |

## CD Daemon

| Key | Required | Default | Purpose |
| --- | --- | --- | --- |
| `CD_IMAGE` | Yes | None | Image repository to track |
| `CD_IMAGE_TAG` | No | `latest` | Tag to watch |
| `CD_CONTAINER_NAME` | No | `codex-slack-master` | Target container name |
| `CD_COMPOSE_FILE` | No | `docker-compose.master-agent.example.yml` | Container-visible path inside the CD runtime to the compose file |
| `CD_COMPOSE_SERVICE` | No | `codex-slack-master` | Compose service name |
| `CD_COMPOSE_BINARY` | No | `podman-compose` | Compose command |
| `CD_ENV_FILE` | No | unset | Container-visible path inside the CD runtime to the env file passed to compose |
| `CD_STATE_FILE` | No | `data/cd/state.json` | Container path inside the CD runtime for persisted daemon state |
| `CD_POLL_INTERVAL_SECONDS` | No | `300` | Registry poll interval |
| `CD_HEALTH_CHECK_DELAY_SECONDS` | No | `30` | Post-deploy health delay |
| `CD_ROLLBACK_ON_FAILURE` | No | `true` | Enable rollback on failed deploy |
| `CD_DRY_RUN` | No | `false` | Disable side effects |
| `CD_NOTIFY_SLACK_WEBHOOK_URL` | No | unset | Slack notification webhook |
| `CD_NOTIFY_DISCORD_WEBHOOK_URL` | No | unset | Discord notification webhook |
