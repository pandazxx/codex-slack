# API Reference

This page lists the implemented command and interaction surfaces documented in this repository.

## Single Bot Slack Commands

- `/codex-status`
- `/codex-attach`
- `/codex-detach`
- `/codex-conv-cancel`
- `/codex-help`

## Master Commands

Implemented command set:

- `/master-agent-list`
- `/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]`
- `/master-agent-start <name>`
- `/master-agent-stop <name>`
- `/master-agent-status <name>`
- `/master-agent-usage [name]`
- `/master-agent-remove <name>`
- `/master-agent-refresh-auth <name>`
- `/master-agent-refresh-config <name>`
- `/master-agent-set-model <name> [model]`

## Interaction Contracts

- Slack routed prompts start with an app mention and continue in thread replies.
- Discord routed prompts start with a mention and continue inside the created Thread channel.
- Master admin commands are valid only in configured admin channels for the corresponding frontend.
- Routed Slack and Discord attachment requests are exposed to the agent through `AGENT_REQUEST_MANIFEST`, not prompt-appended file lists.
- Request manifests can contain staged image attachments plus document-derived Markdown and asset paths under `/workspace/message/<request-id>/...`.
