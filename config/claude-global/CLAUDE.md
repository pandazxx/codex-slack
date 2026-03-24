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

## Returning Files to the User

When you produce a file that the user should receive as a download (e.g. a modified document, a generated report), include an `output_files` key in your JSON response containing the absolute paths of those files inside the container:

```json
{
  "result": "Done — I've updated the spreadsheet.",
  "output_files": ["/workspace/repo/output/report.xlsx"]
}
```

The master runtime will copy those files out of the container and deliver them to the user via the platform (Slack or Discord). Only include files that are complete and ready for the user; do not include intermediate or temporary files.

## Project Scope

This file covers runtime environment and formatting conventions only. Each project is expected to supply its own `.claude/CLAUDE.md` with git workflow, knowledge persistence, project layout, document layout, and the common development workflow. Without a project-scoped file, git workflow rules and documentation conventions will not apply.
