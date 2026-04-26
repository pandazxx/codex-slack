# Master-Agent Interface Design

**Status:** canonical design  
**Scope:** interface contract between the master runtime and agent containers

## Goal

Define the authoritative contract between master and agent so that:

- lifecycle operations are predictable
- runtime injection points are explicit
- request dispatch is adapter-neutral
- agents know which inputs are durable versus transient
- testing and implementation can target stable boundaries

This document complements [`docs/design/agent-container-runtime-design.md`](agent-container-runtime-design.md) by
focusing on the cross-runtime interface rather than the internal agent home
layout.

## Roles

### Master

Master is the control plane. It is responsible for:

- registry ownership
- container lifecycle management
- repo source and branch selection
- shared auth and config injection
- request staging and request-manifest injection
- dispatching prompts into running agent containers

### Agent

Agent is the worker plane. It is responsible for:

- syncing the target repo into `/workspace/repo`
- preparing user-scope runtime state
- executing `codex` or `claude-code`
- consuming request-scoped manifest input
- writing durable project output into `/workspace/repo`

## Interface Categories

The master-agent interface is made of four contract layers:

1. lifecycle contract
2. runtime environment contract
3. dispatch contract
4. transient request-input contract

## 1. Lifecycle Contract

Master controls agent containers through these operations:

- load
- start
- stop
- status
- remove
- refresh-auth
- refresh-config
- set-model
- set-subagent

### Load

Inputs:

- `name`
- `repo_path`
- `channel_id`
- `repo_ref`
- `platform`
- `agent_adapter`

Effects:

- validates mapping and repo source
- resolves image plan
- records agent metadata in registry
- does not start the container

### Start

Inputs:

- logical agent name

Effects:

- builds image when image plan requires it
- creates or recreates the agent container
- injects env and mounts
- starts the container
- updates registry state

### Stop / Remove

Stop controls runtime state; remove clears runtime plus registry mapping.

### Refresh Auth

Master refreshes Codex auth by copying the current host auth seed into the
agent user-scope Codex home without recreating the container.

### Refresh Config

Master refreshes shared Claude defaults by copying the current configured host
directory into the agent user-scope Claude home without recreating the
container.

### Set Model

Master may persist an agent-specific Claude model override in the registry and
inject it on subsequent Claude dispatches.

## 2. Runtime Environment Contract

Master injects these foundational env vars into agent containers:

- `HOME=/workspace/home`
- `XDG_CONFIG_HOME=/workspace/home/.config`
- `CODEX_HOME=/workspace/home/.codex`
- `AGENT_REPO_DIR=repo`
- `AGENT_REPO_URL=<repo source>`
- `AGENT_REPO_REF=<branch>`
- `AGENT_FRONTEND=<slack|discord>`
- `AGENT_ADAPTER=<codex|claude-code>`

Optional shared auth/config env:

- `GH_TOKEN`
- `GITHUB_TOKEN`
- `OPENAI_API_KEY`
- `CLAUDE_CODE_OAUTH_TOKEN`
- `ANTHROPIC_API_KEY`
- `AGENT_GLOBAL_CODEX_CONFIG_DIR`
- `AGENT_GLOBAL_CLAUDE_CONFIG_DIR`
- `SSH_AUTH_SOCK`
- `GIT_SSH_COMMAND`

### Mount Contract

Master mounts into the agent:

- workspace volume -> `/workspace`
- shared Codex config dir -> `/run/secrets/master_codex_config:ro` when set
- shared Claude config dir -> `/run/secrets/master_claude_config:ro` when set
- Codex auth seed -> `/run/secrets/codex_auth.json:ro` when set
- SSH agent socket -> `/run/secrets/ssh-auth.sock` when set
- request storage -> `/workspace/message` for current request-input flow

The workspace volume is durable. Request storage is transient.

## 3. Dispatch Contract

Master dispatches prompts into running agent containers with `podman exec`.

### Dispatch Inputs

Per dispatch, master provides:

- prompt text on stdin
- `AGENT_FRONTEND`
- `AGENT_CHANNEL_ID`
- `AGENT_ADAPTER`
- `AGENT_REQUEST_MANIFEST` when staged attachments exist

For `codex`, dispatch uses the configured Codex command template.

For `claude-code`, dispatch uses the configured Claude command template and may
also inject an explicit model override.

### Dispatch Output

The agent adapter returns one response payload back to master:

- stdout text for Codex
- parsed `result` text from Claude JSON mode when applicable

Master then returns that output to the originating frontend.

### Error Contract

Master is responsible for adapter/runtime failures such as:

- missing container runtime
- timeout
- non-zero adapter exit
- request staging failure

Agent is responsible for errors inside the repo task itself.

## 4. Request Input Contract

When staged input exists, master provides one request manifest:

- env: `AGENT_REQUEST_MANIFEST=/workspace/message/<request-id>/manifest.json`

The manifest is the canonical descriptor for:

- documents
- images
- derived Markdown
- extracted document assets

### Request Input Rules

- `/workspace/message/...` is transient input, not durable project state
- agents must not rely on request storage after the request completes
- commit-worthy output must be copied into `/workspace/repo/...`
- attachments should be consumed from the manifest, not re-guessed from prompt
  text

## Adapter-Specific Differences

### Codex

- primary execution path is `codex exec ...`
- user-scope config lives under `/workspace/home/.codex`
- auth refresh writes to `/workspace/home/.codex/auth.json`
- routed prompts trigger a master-side prepare step before dispatch; if
  `auth_refreshed_at` is missing or older than
  `MASTER_AGENT_AUTH_REFRESH_MAX_AGE_DAYS`, master refreshes Codex auth before
  executing the prompt

### Claude Code

- primary execution path is `claude -p ...`
- user-scope config lives under `/workspace/home/.claude`
- auth comes from env, not a copied auth file
- shared Claude config refresh writes to `/workspace/home/.claude/`
- optional model override may be injected per agent
- optional subagent override may be injected per agent as `--agent <subagent>`

## Observability Contract

Master observes agent behavior through:

- container state
- container logs
- worker status file
- command/dispatch exit codes

Agent does not expose a separate network control API in v1.

## Invariants

The master-agent interface must preserve these invariants:

- master is the only control plane
- agents do not manage other containers
- master is the only source of request-manifest injection
- durable repo state belongs in `/workspace/repo`
- transient request state belongs in `/workspace/message`
- user-scope config lives under `/workspace/home`

## Out of Scope

This document does not define:

- Slack or Discord frontend UX details
- admin command formatting
- repo-specific workflow policies
- human approval or review policy
