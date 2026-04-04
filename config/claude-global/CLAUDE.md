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
- For long replies that need multiple chat messages, insert `🔹🔹🔹` on its own line between sections.
- When using that marker, aim to keep each section around 1700 characters when practical.
- Use the exact marker line only. Do not add numbering or extra text on the marker line.

## Project Scope

This file covers runtime environment and formatting conventions only. Each project is expected to supply its own `.claude/CLAUDE.md` with git workflow, knowledge persistence, project layout, document layout, and the common development workflow. Without a project-scoped file, git workflow rules and documentation conventions will not apply.
