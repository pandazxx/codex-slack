# Agent Container Runtime Design

**Status:** canonical design (v3)
**Scope:** master-managed agent containers for `codex` and `claude-code`

This document supersedes the v2 description in which master forwarded
Slack/Discord events to agents and used `AGENT_FRONTEND` / `AGENT_REQUEST_MANIFEST`
to inject per-frontend metadata and on-disk request manifests. v3 dropped both
chat-platform frontends ([ADR-0006](../decisions/0006-drop-slack-discord-integration.md))
and replaced manifest-mounted attachments with HTTP-fetch attachments delivered
over the v3 master REST API.

## Goal

Define the canonical runtime contract for agent containers so that:

- `codex` and `claude-code` behave consistently inside the same agent image
- shared auth and global configuration are injected predictably
- user-scope and project-scope configuration boundaries are explicit
- guides and runbooks can describe operations without redefining architecture

## Runtime Roles

The same repository ships two runtime roles built from related but distinct images:

- **master runtime** (`Dockerfile`)
  - serves the Vue 3 SPA, REST API, and `/ws/events` WebSocket on port `8080`
  - bridges MQTT events from agents to WebSocket subscribers
  - manages agent container lifecycle through the Docker / Podman socket
  - injects shared auth and configuration into agent containers
- **agent runtime** (`Dockerfile.agent-minimal`, optionally extended via `.prj_assistant/image/Dockerfile`)
  - starts from a per-workspace volume
  - clones or updates the target repo into `/workspace/repo`
  - subscribes to MQTT for prompts; invokes `codex` or `claude-code` per topic
  - publishes streaming reply chunks back over MQTT and uploads attachments via the master REST API

This document covers the agent runtime contract plus the master-to-agent
injection behavior required to make that runtime work.

## Filesystem Layout

Inside a running agent container, the important paths are:

- `/workspace`
  - working area for the agent process; per-topic git worktrees live underneath
- `/workspace/repo`
  - cloned target repository
- `/workspace/worktrees/<topic-id>`
  - per-topic git worktree created on first use of a topic
- `/workspace/home`
  - effective agent home directory
- `/workspace/home/.codex`
  - Codex user-scope home
- `/workspace/home/.claude`
  - Claude Code user-scope home (also bind-mounted from the per-workspace `codex-claude-{workspace_id}` volume so session state survives restarts)
- `/workspace/home/.config`
  - XDG config home

Project-scope overrides stay in the repo:

- `/workspace/repo/.codex`
- `/workspace/repo/.claude`

Agent-fetched attachments for an in-flight prompt are written into the topic
worktree (the agent's current working directory at dispatch time). They are
not delivered through a master-mounted directory in v3.

## Environment Contract

Master configures agent containers with these environment variables (set by
`src/master/agent_runner.py:spawn_agent`):

- `WORKSPACE_ID=<uuid>` — identifies the agent's workspace; used for MQTT topic prefixes
- `MQTT_HOST`, `MQTT_PORT` — broker coordinates inside the compose network
- `MASTER_URL=http://master:8080` — used by the agent to fetch attachments and post results
- `AGENT_REPO_URL=<repo source>`
- `AGENT_REPO_REF=<branch>`
- `GH_TOKEN` (always set when available; falls back through DB-stored runtime config)

One of the following adapter credentials, depending on what the operator configured:

- `CLAUDE_CODE_OAUTH_TOKEN` *or* `ANTHROPIC_API_KEY` — required for Claude Code agents
- `OPENAI_API_KEY` — used by the Codex adapter where applicable

Optional, set only when the operator opts in:

- `GITHUB_TOKEN` — additional GitHub credential injected via DB-stored runtime config
- `SSH_AUTH_SOCK=/run/secrets/ssh-auth.sock` — when master forwards an SSH agent socket
- `GIT_SSH_COMMAND` — set when SSH forwarding is in use

Set inside the agent image / by the entrypoint, not by master:

- `HOME=/workspace/home`
- `XDG_CONFIG_HOME=/workspace/home/.config`
- `CODEX_HOME=/workspace/home/.codex`
- `AGENT_REPO_DIR=repo`

Per-message metadata (topic id, message id, attachment list, prompt body) is
delivered as the MQTT prompt payload, not as environment variables.

## Auth Injection

### Codex

Master injects Codex auth by mounting a host auth cache file into the container
as a read-only secret input:

- source: `MASTER_CODEX_AUTH_JSON_PATH`
- container mount: `/run/secrets/codex_auth.json:ro`

At refresh time, master copies that auth file into the agent user-scope Codex
home:

- destination: `/workspace/home/.codex/auth.json`

This keeps the live writable Codex state inside the agent workspace volume while
using the host file only as a seed/refresh source.

### Claude Code

Master injects Claude auth through environment variables, not by copying a
home-directory auth file:

- preferred: `CLAUDE_CODE_OAUTH_TOKEN`
- fallback: `ANTHROPIC_API_KEY`

This matches the headless container model used by the repository.

## Global Configuration Injection

### Codex

Shared Codex defaults are provided from baked-in image content:

- image path: `/opt/codex-slack/config/codex-global`
- copied into user scope: `/workspace/home/.codex/`

This directory may include files such as:

- `config.toml`
- `AGENTS.md`

These are user-scope defaults for every agent.

### Claude Code

Shared Claude defaults are provided from baked-in image content:

- image path: `/opt/codex-slack/config/claude-global`
- copied into user scope: `/workspace/home/.claude/`

These defaults typically include:

- `settings.json`
- hooks
- shared `CLAUDE.md`-style defaults where applicable

## Project-Scope Override Model

Repo-local configuration is intentionally not promoted into the user-scope home.

Instead:

- Codex reads `/workspace/repo/.codex` as project scope
- Claude Code reads `/workspace/repo/.claude` as project scope

Therefore the precedence model is:

1. project-scoped repo config
2. injected user-scoped global defaults
3. framework defaults

This keeps shared defaults centralized while allowing per-project override
without mutating the user-scope home.

## Request-Scoped Attachment Input

In v3, attachments are delivered to the agent over HTTP, not via mounted
manifest files. The flow:

1. The user uploads a file through the web UI; master stores it via
   `LocalAttachmentStore` and persists metadata in the `attachments` table.
2. When master dispatches the prompt over MQTT, the payload includes an
   `attachments` list with `{id, filename}` entries.
3. The agent (`src/agent/mqtt_loop.py:_fetch_attachment`) downloads each one
   from `{MASTER_URL}/api/attachments/{id}/download` into the topic's git
   worktree (the agent's `cwd` at dispatch time).
4. The agent prepends an attachment note to the prompt text so the LLM is
   aware of the files, then invokes `claude` or `codex`.

Rules:

- The attachment list in the MQTT prompt payload is the canonical request input descriptor.
- Attachments land alongside the worktree as ordinary files; they are not pinned to a separate transient directory.
- If content must survive the request, the agent should commit it into the worktree's branch (the repository's normal git workflow) — there is no separate durable hand-off path.

## Startup and Refresh Flow

### Agent startup

1. master creates or recreates the agent container
2. master mounts workspace volume and secret/config inputs
3. agent worker starts
4. repo sync clones or updates `/workspace/repo`
5. workspace prepare creates `/workspace/home`, `XDG_CONFIG_HOME`, and
   `CODEX_HOME`
6. worker copies shared Codex config into `/workspace/home/.codex/` when
   configured
7. worker copies shared Claude config into `/workspace/home/.claude/` when
   configured
8. worker applies git identity to the repo when configured

### Codex auth refresh

The auth-refresh operation, invoked through `POST /api/workspaces/{id}/refresh-auth`
or the `master-bg` periodic loop, copies the current host Codex auth seed into:

- `/workspace/home/.codex/auth.json`

This refreshes auth without destroying the rest of the Codex home.

Master also runs the same refresh operation during routed-message preparation
when the workspace's `last_refreshed_at` column is null or older than
`MASTER_AGENT_AUTH_REFRESH_MAX_AGE_DAYS` (default `2`). The refresh happens
after any required container startup and before the prompt is dispatched.

### Claude config refresh

The config-refresh operation copies the current shared Claude config directory
into:

- `/workspace/home/.claude/`

This updates Claude defaults without restarting the container.

## Invariants

The runtime must preserve these invariants:

- Codex and Claude user-scope homes both live under `/workspace/home`.
- Repo-local `.codex` and `.claude` remain project-scope inputs only.
- Host auth sources are mounted read-only and copied into writable agent user scope on the first run and on refresh.
- The Claude session volume `codex-claude-{workspace_id}` survives container recreation; deleting it discards Claude session history.
- Durable project output belongs in `/workspace/repo` (or a topic worktree under `/workspace/worktrees/`).
- All cross-process communication between master and agent containers goes through MQTT or the master REST API; the agent does not call back into master via container-runtime mechanisms.

## Out of Scope

This document does not define:

- The REST/WebSocket interface details (see [`docs/references/api.md`](../references/api.md)).
- The MQTT topic schema (see [`docs/decisions/0005-v3-system-architecture.md`](../decisions/0005-v3-system-architecture.md)).
- Release process.
- Project-specific workflow rules.

Those belong in guides, references, and runbooks that depend on this design.
