# Usage Guide

## Operating Model
- You own and prepare the local Codex session.
- The bot attaches to that session and exposes it in Slack.
- Slack is the interaction layer; Codex state remains local.

## Start and Attach
```bash
python -m src.bot.main --session-id <SESSION_ID>
```

The bot becomes active after successful attach and only responds in allowlisted channels.

## Prompt Workflow
1. In an allowed Slack channel, mention the bot in a message or thread.
2. Bot validates channel and enqueues prompt.
3. Bot sends prompt to attached local Codex session.
4. Bot posts one final response to Slack.

Behavior notes:
- Prompts are processed FIFO.
- While busy, new prompts wait in queue.
- Responses are final-only (no streaming chunks).

## Command Reference

### `/codex-status`
Shows current attach state, active session ID, queue depth, and last error.

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
