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
