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

## Knowledge Persistence

Sessions end, context windows fill, and memory resets. The only truly durable record is the repository itself. Treat the repo as the single source of truth for all decisions, discoveries, and fixes — write things down as you go, not just when a task is "done".

*What to record and where:*

- *Architecture decisions* — use the `architect` subagent to produce an ADR (MADR v4) in `docs/decisions/` for every significant choice: technology selected, pattern adopted, approach rejected. If the decision is non-trivial, an ADR is mandatory, not optional.
- *Design documents* — for new features or subsystems, produce a design doc in `docs/design/` before or alongside implementation.
- *Lessons learned and issue post-mortems* — append to `docs/lessons-learned.md` whenever a bug is fixed, a surprising edge case is discovered, or an approach fails. Format each entry as: date, one-line summary, root cause, fix applied, and how to avoid recurrence.
- *Release notes* — maintain a file per release in `docs/releases/` summarising what changed, why, and any migration steps.

*Rules:*

- Never rely on session memory alone for context that spans more than one exchange. If a decision or finding matters beyond this message, write it to the repo.
- After resolving any non-trivial bug or issue, immediately update `docs/lessons-learned.md` and commit it alongside the fix.
- Before starting a significant piece of work, check `docs/decisions/` and `docs/lessons-learned.md` for prior context — do not repeat past mistakes or re-litigate settled decisions.
- If you are asked to resume or continue work and the context is unclear, read the relevant docs files and recent git log first.

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
