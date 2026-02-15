# Slack Integration Setup

This guide configures Slack for local Socket Mode testing with this bot.

## 1. Create Slack App
1. Open `https://api.slack.com/apps`.
2. Click **Create New App**.
3. Choose **From scratch**.
4. Set app name (for example `codex-local-bridge`) and select your workspace.

## 2. Enable Socket Mode
1. In app settings, open **Socket Mode**.
2. Toggle **Enable Socket Mode** on.
3. Create an App-Level Token with scope `connections:write`.
4. Copy the token (`xapp-...`) to use as `SLACK_APP_TOKEN`.

## 3. Configure Bot Token Scopes
1. Open **OAuth & Permissions**.
2. Under **Bot Token Scopes**, add:
- `app_mentions:read`
- `channels:history`
- `chat:write`
- `commands`

## 4. Enable Event Subscriptions
1. Open **Event Subscriptions**.
2. Toggle **Enable Events** on.
3. Under **Subscribe to bot events**, add `app_mention`.
4. Save changes.

Note: With Socket Mode, you do not need to configure a public Request URL.

## 5. Register Slash Commands
Create each command in **Slash Commands**:
- `/codex-status`
- `/codex-attach`
- `/codex-detach`
- `/codex-help`

For each command:
1. Click **Create New Command**.
2. Set command name.
3. For Request URL, enter a placeholder URL (for example `https://example.com/slack/command`).
4. Add a short description.
5. Save.

## 6. Install App to Workspace
1. Open **Install App**.
2. Click **Install to Workspace**.
3. Approve requested permissions.
4. Copy Bot User OAuth Token (`xoxb-...`) as `SLACK_BOT_TOKEN`.

## 7. Invite Bot to Channel
1. In Slack, open your test channel.
2. Run `/invite @<your-bot-name>`.
3. Confirm bot appears in member list.

## 8. Get Channel ID for Allowlist
Use one of the following:
- Channel details UI (copy channel ID).
- Or run:
```bash
curl -sS -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  "https://slack.com/api/conversations.list?types=public_channel,private_channel" | jq
```

Set `SLACK_ALLOWED_CHANNELS` as comma-separated channel IDs.

Example:
```dotenv
SLACK_ALLOWED_CHANNELS=C01234567,C08999999
```

## 9. Local Env Example
```dotenv
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_ALLOWED_CHANNELS=C01234567
CODEX_WORKSPACE_PATH=/absolute/path/to/workspace
```

## 10. Verify Integration
1. Start bot:
```bash
python -m src.bot.main --session-id <SESSION_ID>
```
2. In allowlisted channel, run `/codex-status`.
3. Mention bot: `@codex say hello from Slack`.
4. Confirm a final threaded response appears.

## 11. Common Slack Errors
- `invalid_auth` / `not_authed`: bad or missing tokens.
- `missing_scope`: one or more required scopes not granted.
- Slash command no-op: app not reinstalled after scope/command changes.
- Mention ignored: channel not in `SLACK_ALLOWED_CHANNELS`.
