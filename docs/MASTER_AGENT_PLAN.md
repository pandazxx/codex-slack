# Master-Agent Implementation Plan (Draft)

This is a living phased plan for the master -> agent orchestration feature.

## Scope Boundary (v1)
- Master manages agent container lifecycle.
- Agents remain the current `codex-slack-bot` image.
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
- Finalize v1 start-agent flow: check `.prj_assistant/image/Dockerfile` on project main branch, build if present, otherwise use default image, then clone repo in agent init using named volume.
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

## Phase 1: Local Orchestrator CLI (No Slack Yet)
Goal: prove lifecycle management without Slack complexity.

Build:
- `src/master/registry.py`
- `src/master/runtime_adapter.py`
- `src/master/cli.py`

Commands (local CLI):
- `master agent list`
- `master agent load`
- `master agent start`
- `master agent stop`
- `master agent rm`
- `master agent status`

Validation:
- Can start/stop one agent bound to a test repo and test channel.
- Registry updates survive restart.
- `load` and `start` are independently repeatable/idempotent.
- Agent container initializes a repo clone into named volume workspace.
- Master can report init stage failures using container exit code and recent logs.

## Phase 2: Master Slack Bot (Admin Channel)
Goal: expose orchestrator controls through Slack.

Build:
- `src/master/slack_app.py`
- `src/master/service.py`
- `src/master/main.py`
- channel router (`channel_id -> agent`) + worker dispatch interface
- thread tracking in master for routed agent conversations

Add Slack commands:
- `/master-agent-list`
- `/master-agent-load`
- `/master-agent-start`
- `/master-agent-stop`
- `/master-agent-status`
- `/master-agent-remove`

Validation:
- Commands map cleanly to CLI/service actions.
- Errors are actionable and safe to expose.
- Messages in agent channels route to exactly one agent.
- Unmapped channels receive no agent processing.
- Admin-channel commands do not execute in non-admin channels.
- Thread follow-up replies are routed by master to the same agent without repeated mention.

## Phase 3: Agent Config Templates + Profiles
Goal: reduce repetitive setup for multiple agents.

Build:
- Agent template renderer (compose/run config)
- Profile support for reusable defaults (GH token ref, git identity, runtime)
- Per-agent logging destination conventions

Validation:
- Create multiple agents with minimal arguments.
- Podman runtime path works for both rootful and rootless host socket modes.

## Phase 4: Safety + Operability Hardening
- File locking for concurrent commands.
- Audit log for master actions.
- Rate limiting / command throttling in admin channel.
- Health check and restart policies.
- Secret reference validation and masking in logs.

## Proposed Work Items (Next 3)
1. Draft registry JSON schema and example (`docs/examples/master-agents.schema.json` or markdown table first).
2. Draft project manifest spec (`.prj_assistant/agent.toml`) with image override / Dockerfile options and validation rules (Dockerfile under `.prj_assistant/image/`).
3. Implement local orchestrator CLI with a mock runtime adapter and named-volume workspace init lifecycle.

## Decision Log Seeds (To Finalize Before Phase 1)
- Agent runtime packaging strategy (v1): default image + project manifest image override / repo-local Dockerfile.
- Agent start flow (v1): repo-load -> main branch check -> `.prj_assistant/image/Dockerfile` build-or-default -> start container with named volume -> agent clones repo during init.
- Slack ownership model (v1): one user-managed Slack app/token set for the whole system; master is the only Slack client.
- Slack topology (v1): single Slack app/bot with channel->agent routing via master.
- Channel ownership (v1): strict one-channel-to-one-agent; conflicts rejected unless explicit rebind command is added later.
- Channel identifier input (v1): commands take Slack `channel_id` directly; channel-name lookup deferred.
- Workspace mode (v1): named volume by default; host bind mount optional later.
- Container runtime (v1): Podman only, via host socket mounted into master container.
- Git auth model (v1): master and agent share SSH agent mechanism and/or `GH_TOKEN` refs.
- Agent runtime control model (v1): no always-on service inside agent; master observes status via Podman state + structured logs.
- Branch strategy and repo sync policy: deferred to per-project decisions (out of current scope).
- Codex auth/session model: read-only auth/session forwarding + local `CODEX_HOME` copy; support project-local `.codex`.

## Acceptance Criteria for First PR (Master Project)
- Can register at least one agent in local registry.
- Can render runtime config for that agent.
- Can start/stop the agent container from local CLI.
- Clear docs for setup and limitations.

## Non-Goals (for v1)
- Scheduling/queueing across agents.
- Auto-scaling agents.
- Multi-workspace Slack support.
- Cross-agent prompt routing.
