# Usage Guide

## v2.2 Policy
- This iteration is maintenance-only: bugfixes, docs, and tutorials.
- Avoid introducing net-new features during v2.2 unless explicitly approved.

## Operating Model
- You own and prepare the local Codex session.
- The bot attaches to that session and exposes it in Slack.
- Slack is the interaction layer; Codex state remains local.

## Start and Attach
```bash
python -m src.bot.main --session-id <SESSION_ID>
```

The bot becomes active after successful attach and only responds in allowlisted channels.
If no session id is provided (`--session-id` or `CODEX_SESSION_ID`), the bot auto-generates one and starts with no-session template mode.

For logging destination and level configuration, see `docs/LOGGING.md`.

## Prompt Workflow
1. In an allowed Slack channel, mention the bot in a message or thread.
2. Bot validates channel and enqueues prompt.
3. Bot sends prompt to attached local Codex session.
4. Bot posts one final response to Slack.
5. Subsequent replies in that same thread are accepted without mentioning the bot again.

Behavior notes:
- Prompts are processed FIFO.
- While busy, new prompts wait in queue.
- Responses are final-only (no streaming chunks).

## Master-Mode Workflow (Quick Path)
1. Start master: `python -m src.master.main`.
2. In admin channel, run `/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]`.
3. Run `/master-agent-start <name>`.
4. Prompt from mapped channel by mentioning master bot.
5. Validate state with `/master-agent-status <name>`.

Reference tutorials: `docs/TUTORIALS.md`.

## Naming Convention
- Agent name (`<name>` in master commands):
  - lowercase letters, digits, and hyphen only
  - regex: `[a-z0-9][a-z0-9-]{1,30}`
  - examples: `aidotfile-agent`, `payments-api`
- Slack channel input:
  - use channel ID (for example `C0123456789`), not channel name.
- Runtime-generated resources:
  - container name: `agent-<name>`
  - workspace volume: `agent-workspace-<name>`

## Command Reference

### `/codex-status`
Shows current attach state, active session ID, queue depth, current running prompt, and last error.

### `/codex-attach <session_id>`
Detaches current session (if any) and attaches to the provided local session.

Example:
```text
/codex-attach sess_abc123
```

### `/codex-detach`
Stops forwarding prompts. Local Codex session is not deleted.

### `/codex-help`
Prints command and mention usage summary.

### `/codex-conv-cancel`
Stops the currently running Codex prompt. If no prompt is running, returns a no-op message.

### `/master-agent-refresh-auth <name>`
Re-seeds agent `CODEX_HOME/auth.json` from configured host auth source.
The refresh is non-destructive for session state and does not wipe `.codex`.

### `/master-agent-status <name> --full`
Returns full status JSON split across multiple Slack messages.
Use this when compact status summary is not enough.

### `/master-agent-usage [name]`
Shows per-agent usage counters from master runtime memory:
- prompt count
- prompt/response character volume
- image attachment count
- average latency

## Recovery Playbooks

### Bot reports degraded/error
1. Run `/codex-status` to inspect error.
2. Confirm `codex-cli` is healthy in local terminal.
3. Reattach with `/codex-attach <session_id>`.

### Wrong session attached
1. Find correct local session ID.
2. Run `/codex-attach <correct_session_id>`.

### No channel response
1. Confirm bot was invited to channel.
2. Confirm channel ID is in `SLACK_ALLOWED_CHANNELS`.
3. Check `/codex-status` for auth or attach failures.

## Known Limitations (MVP)
- One active session per bot process.
- No multi-tenant isolation.
- No output streaming; only final reply message.
- Linux/macOS setup is primary path.
