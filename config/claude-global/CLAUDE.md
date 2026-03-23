# Global Agent Instructions

## Environment

This agent runs inside a container. Users do not have direct access to the workspace. The primary interfaces are:

- *GitHub* — commits, pull requests, issues, and tags are the canonical output. Always push and open a PR for the user to review.
- *Reply* — the only real-time channel. Report progress, ask questions, and share links (PR, commit, issue URLs) here.

Never assume the user can inspect files locally.

## Reply Formatting

- Start every reply with `<agent_name> says:` — where `<agent_name>` is `$AI_AGENT_NAME`, or `agent` if unset.
- Check `AGENT_FRONTEND`: if `discord`, apply the `discord_msg_formatter` skill; if `slack` or unset, apply the `slack_msg_formatter` skill.
- Unless the reply is 4 sentences or fewer, structure it into meaningful sections and paragraphs with bold labels.
