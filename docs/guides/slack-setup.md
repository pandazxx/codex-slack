# Slack Integration Setup

This guide configures Slack for the v1 master-agent solution.

v1 Slack model:
- One Slack app for the whole system.
- One bot token and one app token.
- Master is the only Slack client.
- Master commands run in admin channel(s) only.
- Agent channels are normal Slack channels mapped to agents by `channel_id`.

## 1. Create Slack App
1. Open `https://api.slack.com/apps`.
2. Click **Create New App**.
3. Choose **From scratch**.
4. Set app name (for example `codex-master`) and select your workspace.

## 2. Enable Socket Mode
1. Open **Socket Mode**.
2. Toggle **Enable Socket Mode** on.
3. Create an app-level token with scope `connections:write`.
4. Save the token as `SLACK_APP_TOKEN`.

## 3. Configure Bot Token Scopes
Open **OAuth & Permissions** and add these **Bot Token Scopes**:
- `app_mentions:read`
- `channels:history`
- `groups:history`
- `files:read`
- `chat:write`
- `commands`

Notes:
- `app_mentions:read` is required for routed prompts started by mentioning the bot.
- `channels:history` is required for follow-up thread replies in mapped public channels.
- `groups:history` is required for follow-up thread replies in mapped private channels.
- `files:read` is required to fetch Slack private image attachments for agent routing.
- `commands` is required for master slash commands.

## 4. Enable Event Subscriptions
1. Open **Event Subscriptions**.
2. Toggle **Enable Events** on.
3. Under **Subscribe to bot events**, add:
- `app_mention`
- `message.channels`
- `message.groups`
4. Save changes.

With Socket Mode, no public Request URL is needed.

## 5. Register Master Slash Commands
Create these commands in **Slash Commands**:
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

For each command:
1. Click **Create New Command**.
2. Enter the command name.
3. Use a placeholder Request URL, for example `https://example.com/slack/command`.
4. Add a short description.
5. Save.

Load command syntax:
- `/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]`
- Platform is inferred as `slack` because the command runs in Slack.

## 6. Install App to Workspace
1. Open **Install App**.
2. Click **Install to Workspace**.
3. Approve the requested scopes.
4. Save the Bot User OAuth Token as `SLACK_BOT_TOKEN`.

If you add scopes, events, or commands later, reinstall the app.

## 7. Invite Bot to Channels
Invite the bot to:
- each admin channel
- each agent channel that will receive routed prompts

In each target channel:
1. Run `/invite @<your-bot-name>`.
2. Confirm the bot is visible in the member list.

## 8. Get Channel IDs
You need channel IDs for:
- `MASTER_ADMIN_CHANNELS`
- `/master-agent-load <name> <repo_path> <channel_id> [branch]`

Options:
- Copy from Slack channel details UI.
- Or use:
```bash
curl -sS -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  "https://slack.com/api/conversations.list?types=public_channel,private_channel" | jq
```

## 9. Master Environment Example
```dotenv
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
MASTER_FRONTENDS=slack
MASTER_ADMIN_CHANNELS=C01234567
MASTER_REGISTRY_PATH=data/master/agents.json
MASTER_DRY_RUN=false
MASTER_AGENT_COMMAND_TEMPLATE=codex exec --dangerously-bypass-approvals-and-sandbox resume --last -
MASTER_DEFAULT_AGENT_ADAPTER=codex
# The router injects a stable per-thread `resume <session_id>` automatically for standard `... -` templates.
MASTER_AGENT_TIMEOUT_SECONDS=120
MASTER_COMMAND_RATE_LIMIT_COUNT=20
MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS=60
# Optional: mount host ~/.claude directory into agents so claude reads settings.json from there
# MASTER_CLAUDE_CONFIG_DIR_PATH=/home/user/.claude
```

## 10. Verify Master Slack Integration
1. Start master:
```bash
python -m src.master.main
```
2. In an admin channel, run:
```text
/master-agent-list
```
3. Confirm a JSON response is posted.
4. Run (branch optional; defaults to `main`, then falls back to `master`):
```text
/master-agent-load <name> <repo_path> <agent_channel_id> [branch] [--adapter codex|claude-code]
```
5. Run:
```text
/master-agent-start <name>
```
6. If the host Codex auth file is renewed and an existing agent needs to pick it up, run:
```text
/master-agent-refresh-auth <name>
```
7. To override the claude model for a specific agent (takes effect immediately, no restart needed):
```text
/master-agent-set-model <name> claude-opus-4-5
```
Omit the model to clear the override and revert to the default.
8. To override the Claude Code subagent for a specific agent:
```text
/master-agent-set-subagent <name> code-reviewer
```
Omit the subagent to clear the override.
9. In the mapped agent channel, mention the bot with a prompt.
10. Reply in the same thread without mentioning the bot again.
10. Confirm master routes both messages to the mapped agent.

## 11. Channel Usage Rules
- Admin channels are for master slash commands only.
- Non-admin mapped channels are for routed prompts.
- One channel maps to one agent in v1.
- Channel names are not used by commands in v1; use `channel_id`.

## 12. Legacy Standalone Bot Mode
The older standalone bot commands still exist in code:
- `/codex-status`
- `/codex-attach`
- `/codex-detach`
- `/codex-conv-cancel`
- `/codex-help`

Those are for the legacy single-bot flow and are not the primary v1 master-agent setup.

## 13. Common Slack Errors
- `invalid_auth` / `not_authed`: token missing or incorrect.
- `missing_scope`: required scope not granted.
- Slash command no-op: app not reinstalled after command/scope changes.
- Mentions not routed in agent channel: channel not mapped to an agent.
- Master command rejected: command was executed outside `MASTER_ADMIN_CHANNELS`.
