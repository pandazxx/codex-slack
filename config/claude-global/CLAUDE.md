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

Named procedures for common tasks. When the user asks you to perform any of the following, use the Skill tool with the matching skill name. The skill will expand into detailed steps for you to execute using your available tools (Bash, Read, Write, etc.).

- `commit` — stage all changes, write a Conventional Commit message, and push. Use when asked to commit, save, or push changes.
- `pr` — open a pull request against `master` with an auto-generated title and checklist body. Use when asked to open or create a PR.
- `tag` — create and push a git tag; proposes the next version if no name is given. Use when asked to tag a release or cut a tag.
- `reply-formatter` — reformat a draft reply for the current platform (`AGENT_FRONTEND`). Use when asked to format or reformat a reply before sending.

## Subagents

When you need specialised help, use the Agent tool with one of these subagent types. Each runs as an isolated subprocess with full tool access unless noted.

- `doc-writer`: Generates and updates documentation — README files, inline docstrings, changelogs, and architecture notes. Reads current code state and writes clear, accurate docs. Does **not** modify implementation code. (Tools: Read, Grep, Glob, Write)
- `debugger`: Given an error message or unexpected behaviour, performs systematic root-cause analysis — reads logs, traces call stacks, inspects relevant state, and identifies the root cause. Returns a diagnosis and a recommended fix for you to apply. Does **not** modify code. (Tools: Read, Grep, Glob, Bash)
- `test-runner`: Runs the project test suite (auto-detects `pytest`, `npm test`, `go test`, `cargo test`, etc.), parses output, and returns a structured summary of passes, failures, and errors with relevant stack traces. Does **not** modify code. (Tools: Read, Bash)
- `housekeeper`: Scans the codebase for dead code, duplicate functions, unused imports, stale comments, and TODO/FIXME items. Returns a prioritised list of cleanup opportunities with file and line references. Does **not** make changes — reports findings for you to act on. (Tools: Read, Grep, Glob)
