# Repository Guidelines

## Codex Workflow

This repository supports two agent frameworks:
- `.claude/` for Claude Code
- `.agents/skills/` for Codex

When working in Codex, treat this file as the primary repo-level contract and use the repo-local skills under `.agents/skills/` when they match the task.

### Git workflow

- Never commit directly to `master` or `main`.
- Start work from a feature branch named for the task, such as `feat/...`, `fix/...`, `refactor/...`, or `docs/...`.
- Every non-trivial task must end with a commit and push unless the user explicitly says not to commit or not to push.
- Keep the worktree clean at task completion. If that is not possible, stop and explain exactly why.

### Execution model

- Significant features should follow this order: design, implementation, testing, review, documentation sync, commit, push.
- Use the `feature-workflow` skill to run the full lifecycle.
- Use the role-specific skills to keep ownership boundaries clear:
  - `architect`: design, options, tradeoffs, ADR-style outputs
  - `engineer`: implementation on the current branch
  - `tester`: test authoring, execution, and UAT guidance
  - `reviewer`: read-only review and findings
  - `doc-writer`: README and non-`docs/` documentation sync allowed by the current task
  - `commit`, `pr`, `tag`: git workflow helpers

### Current migration constraint

- Do not modify anything under `docs/` unless the user explicitly changes that instruction.
- For this Codex migration, keep the durable workflow artifacts in repo instructions and skills only.

## Project Structure & Module Organization

- `src/` application source code, organized by subdomain: `bot/`, `master/`, `agent/`, `cd/`.
- `tests/` automated tests mirroring `src/` paths.
- `docs/` all documentation — operational runbooks, setup guides, and knowledge-base entries.
- `.claude/` Claude Code agent framework: project CLAUDE.md, subagent definitions, and slash-command skills.
- `.agents/skills/` Codex repo-local skills for workflow orchestration and reusable task guidance.

Example: `src/master/dispatcher.py` should map to `tests/master/test_dispatcher.py`.

## Build, Test, and Development Commands

The project uses Python with pip for dependency management and pytest for testing.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                  # run full test suite
```

For containerised development, see `BUILD.md` and `docs/CONTAINER.md`.

## Coding Style & Naming Conventions

Until language-specific configs are added:
- Use 4 spaces for Python, 2 spaces for JS/TS/JSON/YAML.
- Prefer descriptive names: `feature_action_target` for files where idiomatic.
- Use `PascalCase` for classes/types, `camelCase` for functions/variables, and `UPPER_SNAKE_CASE` for constants.
- Keep modules focused; avoid files that mix unrelated concerns.

Add and enforce formatter/linter configs early (for example, `prettier` + `eslint`, or `ruff` + `black`).

## Testing Guidelines

- Place tests in `tests/` with mirrored paths.
- Name tests by behavior, e.g., `session_expires_after_timeout`.
- Add tests for every bug fix and user-visible behavior change.
- Target meaningful coverage on core logic before raising thresholds.

Run all tests locally before committing.

## Commit & Pull Request Guidelines

Use Conventional Commits:
- `feat: add session token validation`
- `fix: handle missing config file`
- `docs: update onboarding guide`

PRs should include:
- Clear summary of what changed and why.
- Linked issue or ticket when available.
- Test evidence with command and result.
- Screenshots or logs for UI or behavior changes.

When working in Codex, use the repo-local `commit`, `pr`, and `tag` skills.
When working in Claude Code, use `.claude/commands/commit.md` and `.claude/commands/pr.md`.
