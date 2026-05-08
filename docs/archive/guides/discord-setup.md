# Discord Integration Setup (archived)

> **Archived.** This guide describes the v3.0 Discord frontend, which was removed in v3 — see [`docs/decisions/0006-drop-slack-discord-integration.md`](../../decisions/0006-drop-slack-discord-integration.md). Kept for historical reference only; current v3 has no Discord integration.

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
4. Open **Bot** in the left sidebar.
5. Click **Add Bot** if the application does not already have one.
6. Confirm the bot creation prompt.
7. In the same **Bot** page:
   - keep **Public Bot** enabled unless you want to restrict invites manually
   - disable **Require OAuth2 Code Grant** unless you have a specific reason to use it
8. In **Bot > Token**:
   - click **Reset Token** if this is a new bot or you need a fresh token
   - copy the token immediately
   - store it as `DISCORD_BOT_TOKEN`

Notes:
- Discord will only show the full bot token when you create or reset it.
- If you lose it, reset it and update your master runtime env.

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

## 4. Create Admin and Agent Channels
Recommended channel layout:
- One admin channel for control-plane commands
- One or more agent channels for routed conversations

Example:
- `#agent-admin`
- `#agent-api`
- `#agent-docs`

Suggested setup:
1. In your Discord server, click **Add Channel**.
2. Create a private or restricted text channel for admin operations, for example `agent-admin`.
3. Create one text channel per agent workstream, for example:
   - `agent-api`
   - `agent-docs`
   - `agent-ops`
4. Add the bot to any private channels you want it to use.
5. Make sure the bot can:
   - view the channel
   - send messages
   - read message history
   - create/send thread replies if your workflow uses replies/threads

How to add the bot to a channel:
1. Invite the bot to the server first using the OAuth2 URL from **OAuth2 > URL Generator**.
2. For normal channels:
   - ensure the bot's role has server/channel permissions to view and send messages
3. For private channels:
   - open the channel
   - click **Edit Channel**
   - open **Permissions** or **Members**
   - add the bot or its role explicitly
   - grant at least:
     - **View Channel**
     - **Send Messages**
     - **Read Message History**
     - **Send Messages in Threads** if you use replies/threads
4. Test by mentioning the bot in that channel after master is running.

Usage model:
- Admin channel:
  - only for `/master-agent-*` command execution
- Agent channel:
  - mapped to one agent
  - used for prompt routing and follow-up replies

## 4A. Enable Bot-Driven Channel Creation
If you want to use `/master-agent-provision` with Discord `create_channel=true`, the bot must be allowed to create channels in the target location.

Current behavior:
- Discord provisioning creates the new text channel in the same guild/category as the admin channel where you run `/master-agent-provision`.
- The bot therefore needs channel-creation permission in that category, not just in the server generally.

Required permission:
- `Manage Channels`

Recommended setup:
1. Pick the category where provisioned agent channels should be created.
2. Put your Discord admin channel inside that same category if you want provisioning to land there automatically.
3. Open the category in Discord and choose **Edit Category**.
4. Open **Permissions**.
5. Add the bot role or the bot user explicitly.
6. Grant at least:
   - **View Channel**
   - **Send Messages**
   - **Read Message History**
   - **Manage Channels**
7. Save the category permission changes.
8. Verify the bot role is not blocked by another category override.

Recommended validation:
1. Run `/master-agent-provision` from that admin channel.
2. If it fails with Discord error code `50013` (`Missing Permissions`), re-check the category overrides first.

Important:
- Server-level bot permissions alone may not be enough if the category overrides deny `Manage Channels`.
- If you run provisioning from a different admin channel in another category, the new channel will be created there instead.

## 5. Enable Developer Mode and Copy Channel IDs
To obtain channel IDs for `DISCORD_ADMIN_CHANNELS` and `/master-agent-load`:
1. In Discord, open **User Settings**.
2. Open **Advanced**.
3. Enable **Developer Mode**.
4. Return to your server.
5. Right-click the admin channel and choose **Copy Channel ID**.
6. Right-click each agent channel and choose **Copy Channel ID**.

Use those IDs as follows:
- `DISCORD_ADMIN_CHANNELS=<admin_channel_id>`
- `/master-agent-load <name> <repo_path> <agent_channel_id> [branch] [--adapter ...]`

Example:
```dotenv
DISCORD_ADMIN_CHANNELS=123456789012345678
```

## 6. Configure Master Environment
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
MASTER_CLAUDE_COMMAND_TEMPLATE=claude -p --dangerously-skip-permissions
CLAUDE_CODE_OAUTH_TOKEN=...
MASTER_AGENT_TIMEOUT_SECONDS=120
MASTER_COMMAND_RATE_LIMIT_COUNT=20
MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60
# Optional: mount host ~/.claude directory into agents
# MASTER_CLAUDE_CONFIG_DIR_PATH=/home/user/.claude
```

Notes:
- To run Discord only, set `MASTER_FRONTENDS=discord` and omit Slack tokens.
- If both frontends are enabled, both Slack and Discord admin channel allowlists are required.
- For `claude-code` subscription auth in containers, generate `CLAUDE_CODE_OAUTH_TOKEN` on the host with `claude setup-token` and pass it to master.

## 7. Start Master
```bash
python -m src.master.main
```

On startup, master will:
- initialize shared router/service state
- start Slack and/or Discord frontend workers based on `MASTER_FRONTENDS`
- sync Discord application commands when Discord frontend is enabled
- copy global commands into the admin-channel guild for faster slash-command availability

## 8. Discord Command Parity
Discord exposes command parity with Slack:
- `/master-agent-list`
- `/master-agent-load`
- `/master-agent-start`
- `/master-agent-stop`
- `/master-agent-status`
- `/master-agent-usage`
- `/master-agent-remove`
- `/master-agent-refresh-auth`
- `/master-agent-refresh-config`
- `/master-agent-set-model`
- `/master-agent-set-subagent`

`/master-agent-load` supports:
- `name`
- `repo_path`
- `channel_id`
- `branch` (optional)
- `adapter` (`codex` or `claude-code`)

Notes:
- `platform` is inferred automatically from where the command is executed.
- If you run `/master-agent-load` in Discord, the platform is `discord`.
- In Discord admin channels, master also accepts plain text command messages for convenience:
  - `/master-agent-list`
  - `@bot /master-agent-list`

## 9. Verify Discord Routing
1. In a Discord admin channel, run `/master-agent-list`.
2. Run `/master-agent-load` with:
- `channel_id=<target_discord_channel_id>`
3. Run `/master-agent-start <name>`.
4. In the mapped channel, mention the bot with a prompt.
5. Reply in-thread or reply-to-message without re-mentioning.
6. Confirm replies are routed and agent output is posted back.

## 10. Channel Usage Rules
- `DISCORD_ADMIN_CHANNELS` are control-plane channels only.
- Non-admin mapped channels are for routed prompts.
- One channel maps to one agent.
- One agent maps to one channel.

## 11. Common Discord Errors
- `Missing Access` / command not visible:
  - Bot lacks required permissions or app command sync not complete.
- `403 Forbidden (50013): Missing Permissions` during `/master-agent-provision`:
  - Bot lacks `Manage Channels` in the category containing the admin channel used for provisioning.
- Mention messages ignored:
  - Bot not present in channel, or Message Content Intent not enabled.
- Command rejected as non-admin channel:
  - channel ID not included in `DISCORD_ADMIN_CHANNELS`.
- No routing in mapped channel:
  - agent not loaded/started for that Discord `channel_id` with `platform=discord`.
- Adapter execution error:
  - selected adapter command template is invalid in the agent runtime.
