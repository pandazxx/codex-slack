# Agent Container Runtime Design

**Status:** canonical design  
**Scope:** master-managed agent containers for `codex` and `claude-code`

## Goal

Define the canonical runtime contract for agent containers so that:

- `codex` and `claude-code` behave consistently inside the same agent image
- shared auth and global configuration are injected predictably
- user-scope and project-scope configuration boundaries are explicit
- request-scoped attachment input is isolated from durable repo state
- guides and runbooks can describe operations without redefining architecture

## Runtime Roles

The same container image participates in two runtime roles:

- master runtime
  - receives Slack and Discord events
  - manages agent lifecycle through Podman
  - injects shared auth, configuration, and request-scoped inputs into agents
- agent runtime
  - starts from an isolated workspace volume
  - clones or updates the target repo into `/workspace/repo`
  - runs either `codex` or `claude-code`

This document covers the agent runtime contract plus the master-to-agent
injection behavior required to make that runtime work.

## Filesystem Layout

Inside a running agent container, the important paths are:

- `/workspace`
  - persistent workspace volume for the agent
- `/workspace/repo`
  - cloned target repository
- `/workspace/home`
  - effective agent home directory
- `/workspace/home/.codex`
  - Codex user-scope home
- `/workspace/home/.claude`
  - Claude Code user-scope home
- `/workspace/home/.config`
  - XDG config home
- `/workspace/message`
  - request-scoped transient attachment input mounted by master

Project-scope overrides stay in the repo:

- `/workspace/repo/.codex`
- `/workspace/repo/.claude`

## Environment Contract

Master configures agent containers with these important environment variables:

- `HOME=/workspace/home`
- `XDG_CONFIG_HOME=/workspace/home/.config`
- `CODEX_HOME=/workspace/home/.codex`
- `AGENT_REPO_DIR=repo`
- `AGENT_REPO_URL=<repo source>`
- `AGENT_REPO_REF=<branch>`
- `AGENT_FRONTEND=<slack|discord>`
- `AGENT_ADAPTER=<codex|claude-code>`

Optional shared auth and config inputs may also be provided:

- `GH_TOKEN`
- `GITHUB_TOKEN`
- `OPENAI_API_KEY`
- `CLAUDE_CODE_OAUTH_TOKEN`
- `ANTHROPIC_API_KEY`
- `AGENT_GLOBAL_CODEX_CONFIG_DIR=/run/secrets/master_codex_config`
- `AGENT_GLOBAL_CLAUDE_CONFIG_DIR=/run/secrets/master_claude_config`

Per-request dispatch may also inject:

- `AGENT_REQUEST_MANIFEST=/workspace/message/<request>/manifest.json`

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

Shared Codex defaults are provided from a host directory:

- source env: `MASTER_CODEX_CONFIG_DIR_PATH`
- mounted in agent: `/run/secrets/master_codex_config:ro`
- copied into user scope: `/workspace/home/.codex/`

This directory may include files such as:

- `config.toml`
- `instructions.md`

These are user-scope defaults for every agent.

### Claude Code

Shared Claude defaults are provided from a host directory:

- source env: `MASTER_CLAUDE_CONFIG_DIR_PATH`
- mounted in agent: `/run/secrets/master_claude_config:ro`
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

Master may attach transient request input into the agent through
`AGENT_REQUEST_MANIFEST`.

The request data is mounted under:

- `/workspace/message/...`

This area is intentionally transient and must not be treated as durable project
state.

Rules:

- the manifest is the canonical request input descriptor
- document attachments should be consumed through the manifest-derived paths
- image attachments should also be consumed through the manifest, not through
  prompt-appended URLs
- if content must survive the request or be committed, the agent must copy it
  into `/workspace/repo/...` first

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

`/master-agent-refresh-auth <name>` copies the current host Codex auth seed into:

- `/workspace/home/.codex/auth.json`

This is intended to refresh auth without destroying the rest of the Codex home.

### Claude config refresh

`/master-agent-refresh-config <name>` copies the current shared Claude config
directory into:

- `/workspace/home/.claude/`

This updates Claude defaults without restarting the container.

## Invariants

The runtime must preserve these invariants:

- Codex and Claude user-scope homes both live under `/workspace/home`
- repo-local `.codex` and `.claude` remain project-scope inputs only
- host auth/config sources are mounted read-only and copied into writable agent
  user scope
- request-scoped input under `/workspace/message` is transient and master-owned
- durable project output belongs in `/workspace/repo`

## Out of Scope

This document does not define:

- Slack or Discord application setup
- command syntax details for every admin command
- release process
- project-specific workflow rules

Those belong in guides, references, and runbooks that depend on this design.
