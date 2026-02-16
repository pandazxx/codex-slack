# Build Guide

This guide covers local setup for Linux/macOS.

## 1. Prerequisites
- Python 3.11+
- `codex-cli` installed and authenticated
- Slack app with Socket Mode enabled
- Workspace checkout on local machine

## 2. Slack App Setup
Use the full step-by-step guide in `docs/SLACK_SETUP.md`.

Quick checklist:

1. **Socket Mode**: enabled
2. **OAuth scopes** (bot token):
   - `app_mentions:read`
   - `channels:history`
   - `chat:write`
   - `commands`
3. **Event subscriptions**:
   - `app_mention`
4. **Slash commands**:
   - `/codex-status`
   - `/codex-attach`
   - `/codex-detach`
   - `/codex-help`

Install the app to your workspace and collect:
- `SLACK_BOT_TOKEN` (`xoxb-...`)
- `SLACK_APP_TOKEN` (`xapp-...`, Socket Mode)

## 3. Prepare Workspace
```bash
# from repo root
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Configure Environment
Create `.env`:
```dotenv
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_ALLOWED_CHANNELS=C01234567,C08999999
CODEX_WORKSPACE_PATH=/absolute/path/to/workspace
```

## 5. Create or Locate Local Codex Session
Create a session once using your normal `codex` workflow, then reuse its session ID.

Example:
```bash
# start or resume a session interactively, then note the session ID shown by codex
codex resume
```

Default non-interactive command template used by this bot:
```dotenv
CODEX_COMMAND_TEMPLATE=codex exec resume {session_id} -
```

## 6. Start the Bot (Attach Mode)
```bash
python -m src.bot.main --session-id <SESSION_ID>
```

Optional single-channel override:
```bash
python -m src.bot.main --session-id <SESSION_ID> --channel C01234567
```

Logging options:
```bash
# default INFO level to terminal
python -m src.bot.main --session-id <SESSION_ID> --log-level INFO

# save all logs to file
python -m src.bot.main --session-id <SESSION_ID> > bot.log 2>&1
```

See `docs/LOGGING.md` for full logging destination and level configuration.

## 7. Verify Startup
In an allowlisted channel:
1. Run `/codex-status`
2. Send `@codex say hello from slack`
3. Confirm final response appears in thread

## 8. Test Commands
Run unit and smoke checks:
```bash
pytest -q
```

## 9. Troubleshooting
- `invalid_auth` / `not_authed`: verify Slack tokens.
- No response in channel: check channel is in `SLACK_ALLOWED_CHANNELS`.
- Attach failed: confirm `--session-id` exists locally.
- `codex-cli` errors: ensure CLI is installed and logged in.
