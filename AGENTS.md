# Repository Guidelines

## Project Structure & Module Organization
- `src/` application source code, organized by subdomain: `bot/`, `master/`, `agent/`, `cd/`.
- `tests/` automated tests mirroring `src/` paths.
- `docs/` all documentation — operational runbooks, setup guides, and knowledge-base entries.
- `.claude/` Claude Code agent framework: project CLAUDE.md, subagent definitions, and slash-command skills.

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

Run all tests locally before opening a PR.

## Commit & Pull Request Guidelines
Use Conventional Commits:
- `feat: add session token validation`
- `fix: handle missing config file`
- `docs: update onboarding guide`

PRs should include:
- Clear summary of what changed and why.
- Linked issue/ticket when available.
- Test evidence (command + result).
- Screenshots/logs for UI or behavior changes.

Use the `.claude/commands/commit.md` skill and `.claude/commands/pr.md` skill when working inside the Claude Code agent framework.
