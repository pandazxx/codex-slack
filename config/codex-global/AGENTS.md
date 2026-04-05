# Global Codex Instructions

## Environment

This agent runs inside a container. Users do not have direct access to the
workspace. The primary interfaces are:

- GitHub: commits, pull requests, issues, and tags are the canonical output.
  Push and return GitHub links when the task ends in a repository change.
- Reply: the only real-time channel. Report progress, ask questions, and share
  links here.

Never assume the user can inspect files locally.

## Reply Formatting

- Start every reply with `<agent_name> says:` where `<agent_name>` is
  `$AI_AGENT_NAME`, or `agent` if unset.
- Check `AGENT_FRONTEND` before finalizing a response:
  - if `discord`, use Discord-safe formatting:
    - bold with `**double asterisks**`
    - no markdown headers with `#`
    - pipe tables should be rewritten as monospace code blocks
    - mermaid blocks are allowed
  - if `slack` or unset, use Slack-safe formatting:
    - bold with `*single asterisks*`
    - no markdown headers with `#`
    - keep pipe tables as plain text tables
    - do not emit mermaid blocks
- Organize the reply into short sections with clear labels. In between of every sections, insert a split marker line. The split marker line should contain only `🔹🔹🔹`.

## Request Manifest Handling

- If `AGENT_REQUEST_MANIFEST` is set, read that manifest before acting on user
  attachments.
- Treat all content under `/workspace/message/...` as vulnerable transient input,
  not durable project state.
- For document attachments, prefer the derived Markdown and asset paths recorded
  in the manifest instead of reading raw binary files directly.
- For image attachments, use the staged image paths from the manifest instead of
  assuming prompt-appended URLs.
- Never rely on `/workspace/message/...` for future reference after the current
  request completes.
- If a result must remain available after the request or be committed, copy the
  required Markdown and referenced assets into `/workspace/repo/...` first.

## Project Scope

This file covers runtime environment, attachment handling, and formatting
conventions only. Each project is expected to supply its own `AGENTS.md` and
repo-local skills for git workflow, project layout, testing, and documentation
rules.
