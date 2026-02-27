# Master-Agent Implementation Plan (Draft)

This is a living phased plan for the master -> agent orchestration feature.

## Design Freeze Status (v1)
- Status: frozen for implementation.
- Remaining non-blocking item: runtime-config editability from Slack (tracked for further discussion / v1.1).
- Implementation start scope: Phase 1 local orchestrator (`registry`, `runtime_adapter`, `cli`) with Podman runtime path.

## Scope Boundary (v1)
- Master manages agent container lifecycle.
- Agent container is refactored into worker mode from current `codex-slack-bot` baseline.
- No dynamic secret entry via Slack.
- No UI beyond Slack commands and logs.
- Private team / self-managed environment assumptions are acceptable.
- Agent workspaces are stored in named volumes by default (managed clone model).
- Single Slack bot/app is used for both master and agent communications (channel-routed).

## Phase 0: Design Freeze (Short)
- Finalize command set and registry schema.
- Define runtime adapter interface (`podman` in v1).
- Define policy rules (allowed repo roots, channel uniqueness).
- Finalize simplified image override model (`default image` + `project manifest override`).
- Finalize v1 start-agent flow: check `.prj_assistant/image/Dockerfile` on project main branch during `load`, then build on `start` if present, otherwise use default image, then clone repo in agent init using named volume.
- Finalize v1 master command contract and state machine (`load/start/stop/status/list/remove`).
- Finalize channel->agent routing model and admin-channel rules for single-bot mode.
- Finalize channel conflict behavior and thread-routing ownership in master.
- Finalize master container runtime wiring: mounted Podman socket + `podman` client config.
- Finalize agent initialization stages and status reporting contract (no in-agent control service).

Deliverables:
- `docs/MASTER_AGENT_ARCHITECTURE.md`
- `docs/MASTER_AGENT_PLAN.md`
- sample registry JSON
- sample project manifest (`.prj_assistant/agent.toml`)
- master command contract + idempotency rules
- command response envelope + stable error code table
- Slack command success/error examples
- named-volume workspace lifecycle notes
- channel routing rules (`channel_id` ownership and conflict handling)
- admin channel policy + routing rules for non-admin channels

## End-to-End Delivery Plan (v1)
### Phase 1: Master Core (Local CLI Path)
Goal:
- Deliver local lifecycle orchestration with Podman and registry persistence before Slack integration.

Implementation:
- `src/master/registry.py`: JSON source-of-truth, channel conflict lookup, atomic write pattern.
- `src/master/runtime_adapter.py`: Podman adapter (`build/create/start/stop/remove/inspect/logs`) with `--dry-run`.
- `src/master/service.py`: command orchestration (`load/start/stop/status/remove/list`) and error-code mapping.
- `src/master/cli.py`: local operator entrypoint and normalized response envelope.

Validation:
- Can `load/start/stop/status/remove` one agent successfully.
- `load` enforces strict channel conflict behavior.
- Build is triggered by `start` only when Dockerfile plan exists.
- Registry survives process restarts.

### Phase 2: Agent Container Refactor (Worker Mode)
Goal:
- Convert agent runtime from Slack-connected bot to master-driven worker container.

Implementation:
- Split runtime modes:
- `master` mode (Slack-connected) for control plane only.
- `agent-worker` mode (no Slack client) for execution plane.
- Add staged agent entrypoint:
- `preflight` -> `repo_sync` -> `workspace_prepare` -> `ready`.
- Add status signaling contract from agent to master:
- structured stage logs (stdout/stderr markers).
- container exit codes for failed stages.
- optional status file (`/run/master-agent/status.json`) if needed.
- Add per-agent workspace bootstrap:
- clone/fetch repo into named volume.
- initialize isolated `CODEX_HOME`.
- apply shared auth refs (`SSH_AUTH_SOCK`, `GH_TOKEN` file-path refs).

Validation:
- Agent starts without Slack token env.
- Agent can initialize repo workspace and run worker process.
- Failed init stage is visible to master via inspect + logs.

### Phase 3: Master Slack Control Plane (Admin Channel Only)
Goal:
- Expose orchestration commands to Slack admin channel with stable response contract.

Implementation:
- `src/master/slack_app.py`, `src/master/main.py`.
- Map slash commands to service operations:
- `/master-agent-list`
- `/master-agent-load <name> <repo_path> <channel_id>`
- `/master-agent-start <name>`
- `/master-agent-stop <name>`
- `/master-agent-status <name>`
- `/master-agent-remove <name>`
- Enforce admin-channel-only execution for orchestration commands.
- Return stable envelope and error codes in Slack replies.

Validation:
- Slack command outputs match local CLI/service outcomes.
- Unauthorized channel usage is rejected cleanly.

### Phase 4: Message Routing and Thread Continuity
Goal:
- Route agent-channel prompts through master to mapped agent, preserving thread behavior.

Implementation:
- Implement `channel_id -> agent` router with strict 1:1 mapping.
- Keep master as the only Slack event consumer.
- Forward non-admin channel prompts to mapped agent worker interface.
- Preserve thread continuity:
- initial mention creates tracked thread.
- follow-up replies route to same mapped agent without repeated mention.

Validation:
- Unmapped channels are ignored or receive setup hint per policy.
- Mapped channels always route to exactly one agent.
- Thread follow-up behavior remains consistent with existing UX.

### Phase 5: Operability, Safety, and Release Readiness
Goal:
- Harden v1 for team operation and prepare release PR.

Implementation:
- File locking for concurrent registry mutations.
- Audit log for master actions (`request_id`, command, actor/channel, result code).
- Rate limiting in admin channel.
- Secret-path validation and masking in logs.
- Podman socket mode verification (rootful/rootless).
- Runbooks for bootstrap, recovery, and manual unbind/bind.

Validation:
- Failure scenarios covered:
- build failure
- start failure
- container crash
- Slack outage/reconnect
- E2E smoke test: load -> start -> route prompt -> stop -> remove.

## Immediate Backlog (Execution Order)
1. Finalize registry schema doc and add sample payload (`docs/examples/master-agents.schema.json` or markdown table).
2. Complete service error normalization and response envelope parity between CLI and Slack adapters.
3. Implement agent worker mode entrypoint and staged initialization contract.
4. Implement master-to-agent dispatch interface (non-Slack control path).
5. Wire admin-channel slash commands to master service.
6. Add channel routing + thread continuity integration tests.
7. Add runbook docs for operational flows and failure handling.

## Decision Log Seeds (To Finalize Before Phase 1)
- Agent runtime packaging strategy (v1): default image + project manifest image override / repo-local Dockerfile.
- Agent start flow (v1): repo-load -> main branch check -> plan build-or-default -> start triggers Dockerfile build when needed -> start container with named volume -> agent clones repo during init.
- Slack ownership model (v1): one user-managed Slack app/token set for the whole system; master is the only Slack client.
- Slack topology (v1): single Slack app/bot with channel->agent routing via master.
- Command surface (v1): master orchestration commands run in admin channel only.
- Secret reference model (v1): file-path references (no raw secrets in commands).
- Channel ownership (v1): strict one-channel-to-one-agent; conflicts resolved via manual unbind then bind/load.
- Channel identifier input (v1): commands take Slack `channel_id` directly; channel-name lookup deferred.
- Workspace mode (v1): named volume by default; host bind mount optional later.
- Container runtime (v1): Podman only, via host socket mounted into master container.
- Git auth model (v1): master and agent share SSH agent mechanism and/or `GH_TOKEN` refs.
- Agent runtime control model (v1): no always-on service inside agent; master observes status via Podman state + structured logs.
- Branch strategy and repo sync policy: deferred to per-project decisions (out of current scope).
- Codex auth/session model (v1): shared token/auth reference + isolated per-agent `CODEX_HOME`.

## Acceptance Criteria for First PR (Master Project)
- Can register at least one agent in local registry.
- Can render runtime config for that agent.
- Can start/stop the agent container from local CLI.
- Clear docs for setup and limitations.

## V1 Release Acceptance Criteria (End-to-End)
- Master runs in container and controls host Podman via mounted socket.
- Admin-channel slash commands manage lifecycle end-to-end.
- Agent container runs in worker mode with no direct Slack connectivity.
- Agent init stages are observable via status and structured logs.
- Channel mapping remains strict one-channel-to-one-agent.
- Build-on-start behavior works for `.prj_assistant/image/Dockerfile`.
- Shared auth reference + isolated per-agent `CODEX_HOME` behavior verified.
- E2E flow passes: load -> start -> prompt route -> response -> stop -> remove.

## Non-Goals (for v1)
- Scheduling/queueing across agents.
- Auto-scaling agents.
- Multi-workspace Slack support.
- Cross-agent prompt routing.
