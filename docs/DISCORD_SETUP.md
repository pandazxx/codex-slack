# Discord Integration Setup

This guide configures Discord for the v3.0 master-agent frontend.

v3 Discord model:
- One Discord bot application for the system.
- Master handles both control-plane commands and routed prompts.
- Admin commands are allowed only in `DISCORD_ADMIN_CHANNELS`.
- Agent channels are mapped by Discord `channel_id`.

## 1. Create Discord Application
1. Open `https://discord.com/developers/applications`.
2. Click **New Application**.
3. Set application name (for example `codex-master`).
4. Open **Bot** and create/add the bot user.

## 2. Configure Bot Intents
In **Bot > Privileged Gateway Intents**, enable:
- **Message Content Intent**

This is required so the master can read mention and follow-up prompt text.

## 3. Configure OAuth2 Scopes and Permissions
Open **OAuth2 > URL Generator** and select scopes:
- `bot`
- `applications.commands`

Recommended bot permissions:
- View Channels
- Send Messages
- Read Message History
- Embed Links
- Attach Files
- Send Messages in Threads (if thread usage is enabled)

Use the generated URL to invite the bot to your server.

## 4. Collect Required IDs
Enable Discord developer mode and copy IDs for:
- Admin channels (for `DISCORD_ADMIN_CHANNELS`)
- Agent channels (for `/master-agent-load ... --platform discord`)

## 5. Configure Master Environment
```dotenv
MASTER_FRONTENDS=slack,discord

# Slack frontend (optional if using discord-only)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
MASTER_ADMIN_CHANNELS=C01234567

# Discord frontend
DISCORD_BOT_TOKEN=...
DISCORD_ADMIN_CHANNELS=123456789012345678

MASTER_REGISTRY_PATH=data/master/agents.json
MASTER_THREAD_STATE_PATH=data/master/thread_state.json
MASTER_DRY_RUN=false

MASTER_DEFAULT_AGENT_ADAPTER=codex
MASTER_CODEX_COMMAND_TEMPLATE=codex exec --dangerously-bypass-approvals-and-sandbox resume --last -
MASTER_CLAUDE_COMMAND_TEMPLATE=claude -p
MASTER_AGENT_TIMEOUT_SECONDS=120
MASTER_COMMAND_RATE_LIMIT_COUNT=20
MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60
```

Notes:
- To run Discord only, set `MASTER_FRONTENDS=discord` and omit Slack tokens.
- If both frontends are enabled, both Slack and Discord admin channel allowlists are required.

## 6. Start Master
```bash
python -m src.master.main
```

On startup, master will:
- initialize shared router/service state
- start Slack and/or Discord frontend workers based on `MASTER_FRONTENDS`
- sync Discord application commands when Discord frontend is enabled

## 7. Discord Command Parity
Discord exposes command parity with Slack:
- `/master-agent-list`
- `/master-agent-load`
- `/master-agent-start`
- `/master-agent-stop`
- `/master-agent-status`
- `/master-agent-usage`
- `/master-agent-remove`
- `/master-agent-refresh-auth`

`/master-agent-load` supports:
- `name`
- `repo_path`
- `channel_id`
- `branch` (optional)
- `platform` (`slack` or `discord`)
- `adapter` (`codex` or `claude-code`)

## 8. Verify Discord Routing
1. In a Discord admin channel, run `/master-agent-list`.
2. Run `/master-agent-load` with:
- `platform=discord`
- `channel_id=<target_discord_channel_id>`
3. Run `/master-agent-start <name>`.
4. In the mapped channel, mention the bot with a prompt.
5. Reply in-thread or reply-to-message without re-mentioning.
6. Confirm replies are routed and agent output is posted back.

## 9. Channel Usage Rules
- `DISCORD_ADMIN_CHANNELS` are control-plane channels only.
- Non-admin mapped channels are for routed prompts.
- One channel maps to one agent.
- One agent maps to one channel.

## 10. Common Discord Errors
- `Missing Access` / command not visible:
  - Bot lacks required permissions or app command sync not complete.
- Mention messages ignored:
  - Bot not present in channel, or Message Content Intent not enabled.
- Command rejected as non-admin channel:
  - channel ID not included in `DISCORD_ADMIN_CHANNELS`.
- No routing in mapped channel:
  - agent not loaded/started for that Discord `channel_id` with `platform=discord`.
- Adapter execution error:
  - selected adapter command template is invalid in the agent runtime.
