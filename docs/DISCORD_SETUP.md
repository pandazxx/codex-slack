# Discord Integration Setup

This guide configures Discord for the master-agent solution.

Discord model:
- One Discord bot application for the whole system.
- One bot token.
- Master is the only Discord client.
- Master commands run in admin channel(s) only (configured via `DISCORD_ADMIN_CHANNELS`).
- Agent channels are normal Discord text channels mapped to agents by `channel_id`.
- Conversations are tracked via Discord native Threads. When the bot is @mentioned in a regular channel, it creates a Thread from its acknowledgement message. Follow-up messages inside that Thread are automatically routed to the same agent without re-mentioning.

## 1. Create a Discord Application
1. Open [https://discord.com/developers/applications](https://discord.com/developers/applications).
2. Click **New Application**.
3. Set a name (for example `codex-master`) and click **Create**.

## 2. Create a Bot User
1. Open **Bot** in the left sidebar.
2. Click **Add Bot** → **Yes, do it!**.
3. Under **Token**, click **Reset Token**, then copy and save it as `DISCORD_BOT_TOKEN`.
4. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent** (required to read message text)
   - **Server Members Intent** (optional, not required for routing)

## 3. Configure Bot Permissions
Open **OAuth2 → URL Generator**:

1. Under **Scopes**, select `bot`.
2. Under **Bot Permissions**, select:
   - `Read Messages / View Channels`
   - `Send Messages`
   - `Send Messages in Threads`
   - `Create Public Threads`
   - `Read Message History`
   - `Attach Files` (required if replies exceed Discord message limit)
3. Copy the generated URL and open it to invite the bot to your server.

## 4. Get Channel IDs
Enable Developer Mode in Discord (**User Settings → Advanced → Developer Mode**), then right-click any channel and select **Copy Channel ID**.

You need channel IDs for:
- `DISCORD_ADMIN_CHANNELS`
- `/master-agent-load <name> <repo_path> <channel_id> [branch]`

## 5. Enable Master Frontend
Set `MASTER_FRONTENDS` to include `discord` (can be combined with `slack`):

```dotenv
MASTER_FRONTENDS=discord
# or for both:
MASTER_FRONTENDS=slack,discord
```

## 6. Master Environment Example
```dotenv
DISCORD_BOT_TOKEN=MTExxx...
DISCORD_ADMIN_CHANNELS=1234567890123456789
MASTER_FRONTENDS=discord
MASTER_REGISTRY_PATH=data/master/agents.json
MASTER_DRY_RUN=false
MASTER_AGENT_COMMAND_TEMPLATE=codex exec --dangerously-bypass-approvals-and-sandbox resume --last -
MASTER_AGENT_TIMEOUT_SECONDS=120
MASTER_COMMAND_RATE_LIMIT_COUNT=20
MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60
# Optional: mount host ~/.claude directory into agents
# MASTER_CLAUDE_CONFIG_DIR_PATH=/home/user/.claude
```

## 7. Invite Bot to Channels
In each Discord server channel where the bot should operate:
1. Open channel settings → **Permissions**.
2. Confirm the bot role has **View Channel**, **Send Messages**, **Send Messages in Threads**, **Create Public Threads**, and **Read Message History**.

## 8. Verify Discord Integration
1. Start master with Discord frontend enabled.
2. In a configured admin channel, run a slash command (if slash commands are registered — see note below) or check master logs for `master.frontend_started name=discord`.
3. In a mapped agent channel, @mention the bot with a prompt.
4. Confirm the bot creates a Thread and posts its acknowledgement inside it.
5. Reply inside the Thread (no @mention needed).
6. Confirm master routes the follow-up to the same agent.

> **Note on slash commands:** Discord slash commands require registration via the Discord application portal or the Discord API. The master currently processes slash commands natively on Slack. For Discord admin operations, use the Slack frontend in parallel, or register Discord slash commands separately pointing to your bot's application ID.

## 9. Thread-Based Conversation Tracking
Unlike Slack threads (which use `thread_ts`), Discord uses native Thread channels:

- When the bot is @mentioned in a regular text channel, it creates a Discord Thread from its ack message.
- `channel_id` (the parent text channel) is used to look up the mapped agent.
- `thread_id` (the Thread channel ID) is tracked so follow-up messages inside the Thread are auto-routed.
- Users reply inside the Thread — no repeated @mention is needed.

This is the primary difference from the Slack UX: conversations happen inside a Discord Thread, not a Slack thread.

## 10. Common Discord Errors
- `discord.errors.Forbidden`: bot is missing required permissions in the channel.
- Bot not responding to @mentions: **Message Content Intent** is not enabled in the Developer Portal.
- Replies posted as files instead of text: message exceeded 4000 characters; this is expected behavior for long responses.
- Thread not created: bot lacks **Create Public Threads** permission.
- Messages in Thread not routed: the Thread was not created by this bot session (thread state is in-memory; restarting master clears tracked threads).
