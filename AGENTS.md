# Repository Guidelines

## Project Structure & Module Organization
This repository is currently in bootstrap state (no committed source files yet). Keep the layout simple and predictable as code is added:
- `src/` application code, organized by feature or domain.
- `tests/` automated tests mirroring `src/` paths.
- `docs/` design notes, architecture decisions, and runbooks.
- `assets/` static files (images, sample data).

Example: `src/auth/session.ts` should map to `tests/auth/session.test.ts`.

## Build, Test, and Development Commands
No build system is committed yet. When adding one, expose standard entry points through a single toolchain (`Makefile` or package scripts) and document them here.
Recommended baseline commands:
- `make setup` install dependencies.
- `make test` run the full test suite.
- `make lint` run static analysis and formatting checks.
- `make dev` start the local development environment.

If you use Node, also provide `npm run test`, `npm run lint`, and `npm run dev` equivalents.

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
There is no existing commit history yet; start with Conventional Commits:
- `feat: add session token validation`
- `fix: handle missing config file`

PRs should include:
- Clear summary of what changed and why.
- Linked issue/ticket when available.
- Test evidence (command + result).
- Screenshots/logs for UI or behavior changes.
