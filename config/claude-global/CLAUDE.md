# Global Agent Instructions

This file is installed at *user scope* (`~/.claude/CLAUDE.md`) and applies to every project
this agent works on. Individual repositories can extend or override these defaults by placing
a `.claude/CLAUDE.md` file in the repository root (project scope). Claude Code concatenates
all in-scope CLAUDE.md files, so project-scope instructions are appended after these and
take effect alongside them.

Settings in `settings.json` follow the same two-tier model:
- *User scope* (`~/.claude/settings.json`) — this file, managed by the master deployment.
- *Project scope* (`.claude/settings.json` in the repo) — per-project overrides that take
  precedence over user-scope values for any key they define.

## Environment

This agent runs inside a container. Users do not have direct access to the workspace filesystem. The primary interfaces for delivering work are:

- *GitHub* — commits, pull requests, issues, and tags are the canonical way to surface completed work. Always push and open a PR so the user can review changes.
- *Reply* — the chat reply is the only real-time channel to the user. Use it to report progress, ask questions, share links (PR URL, commit URL, issue URL), and summarise what was done.

Never assume the user can inspect files locally. If something is important for the user to see, include it in the reply or commit it to the repo.

## Git Workflow

- *Never* commit directly to `master` or `main`. Always check your current branch before making any commits.
- If you are on `master` or `main`, create a new feature branch first: `git checkout -b <descriptive-branch-name>`.
- Name branches clearly after the work being done, e.g. `feat/add-login`, `fix/null-pointer`, `refactor/cleanup-auth`.
- After completing your changes on a feature branch, push the branch and open a pull request targeting `master` or `main`.
- Use `gh pr create --base master --fill` (or `--base main`) to submit the PR.

## Committing and Pushing Changes

- After every meaningful set of changes, stage, commit, and push.
- Do not leave work uncommitted. Every code change must be committed and pushed before you consider the task done.
- Write concise, descriptive commit messages that explain *what* changed and *why*.
- Example: `git add -p && git commit -m "fix: handle null session in auth middleware" && git push`.

## Reply Formatting

- Always start every reply with `<agent_name> says:` where `<agent_name>` is the value of the `AI_AGENT_NAME` environment variable. If the variable is not set, use `agent` as the name.
- Check the `AGENT_FRONTEND` environment variable to determine the platform and apply platform-specific formatting rules below.
- Use `-` for bullet lists. Do not use `#` or `##` headers — use bold text as section labels instead.
- Use backticks for inline code: `like this`. Use triple backticks for code blocks.
- Do not use HTML, horizontal rules (`---`), or deep nesting.
- Keep responses clear and readable in a chat window. There is no length restriction — be as thorough as needed, but avoid unnecessary padding.

*If AGENT_FRONTEND=discord:*
- Use `**bold**` (double asterisk) for emphasis.
- For architecture diagrams, flow charts, or sequence diagrams, use mermaid code blocks (` ```mermaid `). These are rendered as images automatically.

*If AGENT_FRONTEND=slack (or unset):*
- Use `*bold*` (single asterisk) for emphasis.
- Do not use mermaid blocks — plain text or ASCII diagrams only.

## Skills

Named procedures for common tasks, defined in `~/.claude/commands/`. Invoke them with the Skill tool (non-interactive mode) or `/skill-name` (interactive mode). Pass any relevant context as the argument.

- `commit` — stage all changes, write a Conventional Commit message, and push.
- `pr` — open a pull request against `master` with an auto-generated title and checklist body.
- `tag` — create and push a git tag; proposes the next version if no name is given.
- `reply-formatter` — reformat a draft reply for the current platform (`AGENT_FRONTEND`).

## Subagents

Specialised agents defined in `~/.claude/agents/`. Spawn them with the Agent tool when you need focused help. Each runs in isolation and does not modify code unless its description says so.

- `architect` — principal engineer: plans solutions, evaluates tradeoffs, and produces ADRs (MADR v4) and design documents. Use when designing a new system or documenting a significant decision.
- `doc-writer` — write or update documentation without touching implementation files.
- `debugger` — root-cause analysis for errors; returns a diagnosis and recommended fix.
- `test-runner` — run the test suite and return a structured pass/fail summary.
- `housekeeper` — scan for dead code, unused imports, TODOs, and duplicate logic; returns a prioritised cleanup list.
