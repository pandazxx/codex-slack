# Operation: Run tests

## Inputs
- `<pattern>` — optional pytest `-k` pattern (e.g. `test_image_contract`); `""` for the whole suite
- `<db>` — optional backend: `sqlite` (default), `postgres`, or `mysql`. Use `postgres` when the user says "against Postgres" or is testing DB behaviour.

## Required env vars
- `DEV_DOCKER_HOST`

## Pre-conditions
- (none beyond standard pre-flight)

## Steps

1. Run exactly one of:
   ```
   just test                       # sqlite, whole suite
   just test "<pattern>"           # sqlite, filtered
   just test "" postgres           # postgres, whole suite
   just test "<pattern>" postgres  # postgres, filtered
   just test "" mysql              # mysql (expected to fail until Phase 2; report, do not escalate as a regression)
   ```
   The recipe builds the test stage on `DEV_DOCKER_HOST` in an ephemeral compose project, starts `postgres:17-alpine` or `mysql:8.0` for non-sqlite runs (two databases: `master` for the whole suite via `TEST_DATABASE_URL`, `master_xdialect` for cross-dialect tests via `TEST_POSTGRES_URL`/`TEST_MYSQL_URL`), runs `pytest`, and tears everything down on exit. It never touches a branch dev env or its data.

## On failure
- Non-zero exit: pytest output is already in stdout. Pass through and escalate if tests fail.
- Build failure or database not healthy: escalate immediately.
- Never run tests against a dev env's own `postgres`; the recipe uses its own throwaway one.

## Output
The recipe's stdout is the user-facing output. Pass through verbatim.
