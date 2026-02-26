# Master-Agent Implementation Plan (Draft)

This is a living phased plan for the master -> agent orchestration feature.

## Scope Boundary (v1)
- Master manages agent container lifecycle.
- Agents remain the current `codex-slack-bot` image.
- No dynamic secret entry via Slack.
- No UI beyond Slack commands and logs.
- Private team / self-managed environment assumptions are acceptable.
- Agent workspaces are stored in named volumes by default (managed clone model).

## Phase 0: Design Freeze (Short)
- Finalize command set and registry schema.
- Define runtime adapter interface (`docker` and `podman`).
- Define policy rules (allowed repo roots, channel uniqueness).
- Finalize simplified image override model (`default image` + `project manifest override`).
- Finalize v1 start-agent flow: check `.prj_assistant/image/Dockerfile` on project main branch, build if present, otherwise use default image, then clone repo in agent init using named volume.
- Finalize v1 master command contract and state machine (`load/start/stop/status/list/remove`).

Deliverables:
- `docs/MASTER_AGENT_ARCHITECTURE.md`
- `docs/MASTER_AGENT_PLAN.md`
- sample registry JSON
- sample project manifest (`.prj_assistant/agent.toml`)
- master command contract + idempotency rules
- named-volume workspace lifecycle notes

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

## Phase 2: Master Slack Bot (Admin Channel)
Goal: expose orchestrator controls through Slack.

Build:
- `src/master/slack_app.py`
- `src/master/service.py`
- `src/master/main.py`

Add Slack commands:
- `/master-agent-list`
- `/master-agent-load`
- `/master-agent-start`
- `/master-agent-stop`
- `/master-agent-status`

Validation:
- Commands map cleanly to CLI/service actions.
- Errors are actionable and safe to expose.

## Phase 3: Agent Config Templates + Profiles
Goal: reduce repetitive setup for multiple agents.

Build:
- Agent template renderer (compose/run config)
- Profile support for reusable defaults (GH token ref, git identity, runtime)
- Per-agent logging destination conventions

Validation:
- Create multiple agents with minimal arguments.
- Podman and Docker variants both supported.

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
- Slack ownership model: user-managed Slack apps/tokens; master validates and orchestrates only.
- Workspace mode (v1): named volume by default; host bind mount optional later.
- Git auth model (v1): master and agent share SSH agent mechanism and/or `GH_TOKEN` refs.
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
