# Master-Agent Architecture (Draft)

This is a living design document for extending this project from a single Slack-connected agent into a master -> agent orchestration model.

## Goal
- Keep the current container as the **agent runtime**.
- Introduce a **master container** that accepts control commands from Slack and manages agent containers for different repos/channels.

## Roles
### Master Container (Control Plane)
- Listens in one admin Slack channel.
- Creates/starts/stops/removes agent containers.
- Maintains agent registry/state on disk.
- Applies policy checks (allowed repo paths, channel mapping, naming).
- Surfaces status/logs back to Slack.

### Agent Container (Worker Plane)
- Existing `codex-slack-bot` image.
- One target repo per container.
- One allowlisted Slack channel per container.
- Runs Codex prompts for that project only.

## Core Principle
- Separate orchestration from execution.
- Master should not execute user coding prompts.
- Agents should not manage other containers.

## Proposed Slack UX (Master)
- `/master-agent-list`
- `/master-agent-create <name> <repo_path> <channel_id>`
- `/master-agent-start <name>`
- `/master-agent-stop <name>`
- `/master-agent-rm <name>`
- `/master-agent-status <name>`
- `/master-agent-logs <name>`
- `/master-agent-config <name> key=value`

## Runtime Interface (Master -> Container Engine)
Support both Docker and Podman via a thin adapter:
- `create_or_update_agent(config)`
- `start_agent(name)`
- `stop_agent(name)`
- `remove_agent(name)`
- `inspect_agent(name)`
- `tail_logs(name, lines)`

Start with CLI invocation (`docker` / `podman`), not daemon APIs.

## Registry (Initial)
Store in repo-local data files:
- `data/master/agents.json` (source of truth)
- `build/agents/<name>.compose.yml` (rendered runtime config)

Per-agent fields (v1):
- `name`, `repo_path`, `channel_id`, `container_name`
- `image`, `runtime` (`docker|podman`)
- `codex_session_id` (optional)
- `git_user_name`, `git_user_email` (optional)
- `gh_token_ref` (optional; do not store raw secret in registry)
- `status` (observed), `created_at`, `updated_at`

## Security Boundaries
- One Slack app for master, separate Slack apps for agents (recommended).
- No raw secrets entered in Slack commands.
- Secrets injected from host env/files or secret store references.
- Restrict master-managed repo paths to approved prefixes.
- Restrict generated container names to safe pattern.

## Failure Model
- Master command succeeds but agent fails to start:
  - Registry persists desired state + error message.
- Agent crashes:
  - Master reports status and restart option.
- Slack outage:
  - Master/agents continue local state; recover on reconnect.

## Open Design Questions
1. Should master use slash commands only, or admin-channel mentions too?
2. How should secret references be represented (env key name vs file path)?
3. Do we allow shared Codex auth/session mounts for all agents, or per-agent scoped homes by default?
4. How much of runtime config should be user-editable from Slack?
