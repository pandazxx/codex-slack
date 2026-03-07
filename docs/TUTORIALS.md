# Tutorials

## v2.2 Housekeeping Scope
This cycle is documentation-first and bugfix-only. No new feature development is planned.

## Tutorial 1: Boot a Single Bot Session
1. Export required env vars: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_ALLOWED_CHANNELS`.
2. Start bot: `python -m src.bot.main --session-id <SESSION_ID>`.
3. In Slack, mention the bot in an allowlisted channel.
4. Confirm response and run `/codex-status`.

## Tutorial 2: Master Agent Command Flow
1. Start master: `python -m src.master.main` with `MASTER_ADMIN_CHANNELS` configured.
2. Load an agent:
   - `/master-agent-load <name> <repo_path> <channel_id> [branch]`
3. Start agent:
   - `/master-agent-start <name>`
4. In the mapped channel, mention the master bot with a prompt.
5. Validate runtime state:
   - `/master-agent-status <name>`

## Tutorial 3: Safe Auth Refresh
Use `/master-agent-refresh-auth <name>` when rotating Codex auth.

Expected behavior:
- Updates `CODEX_HOME/auth.json` in the agent workspace.
- Preserves existing Codex session state files under `.codex`.

## Tutorial 4: Day-2 Troubleshooting
1. Check command output payload (`ok`, `code`, `message`, `data`).
2. Verify agent status and runtime inspection.
3. Tail container logs when needed.
4. Re-run auth refresh or restart agent if auth/runtime drift is detected.

## Release Wrap-Up Checklist (v2.2)
- [ ] No net-new features merged.
- [ ] Bugfixes include tests.
- [ ] README and USAGE reflect current command behavior.
- [ ] Tutorials validated against current `master`.
- [ ] Release candidate notes drafted.
