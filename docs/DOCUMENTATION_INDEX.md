# Documentation Index (Canonical References)

Use this page as the source-of-truth map for v3.0.

## Current Source of Truth
- Product overview and command summary: `README.md`
- Operator runbook (master/agent runtime): `docs/MASTER_AGENT_RUNBOOK.md`
- Slack app setup for master mode: `docs/SLACK_SETUP.md`
- Discord app setup for master mode: `docs/DISCORD_SETUP.md`
- Build/setup for local bot mode: `BUILD.md`
- Day-to-day usage and troubleshooting: `USAGE.md`
- Hands-on tutorials and checklists: `docs/TUTORIALS.md`

## Canonical Master Command Set (Implemented)
- `/master-agent-list`
- `/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]`
- `/master-agent-start <name>`
- `/master-agent-stop <name>`
- `/master-agent-status <name>`
- `/master-agent-usage [name]`
- `/master-agent-remove <name>`
- `/master-agent-refresh-auth <name>`

## Important Behavior Notes
- `/master-agent-refresh-auth` updates agent `CODEX_HOME/auth.json` and preserves existing `.codex` session state.
- Project-specific image build is triggered on `start` when `.prj_assistant/image/Dockerfile` exists.
- Master admin commands are valid only in `MASTER_ADMIN_CHANNELS`.
- Mapped non-admin channels are used for routed prompts.

## Historical/Draft Docs
The following are design artifacts and may include non-implemented options:
- `docs/MASTER_AGENT_ARCHITECTURE.md`
- `docs/MASTER_AGENT_PLAN.md`
- `docs/V3_0_MULTI_ADAPTER_FRONTEND_PLAN.md`

Treat them as context/history, not operational source-of-truth.
