# Operation: Spin up dev env

## Inputs
- `<branch>` — git branch name from the user's request

## Required env vars
- `DEV_DOCKER_HOST` — e.g. `ssh://ubuntu@10.10.10.238`
- (optional) `DEV_POSTGRES_PASSWORD` — overrides the dev-only default `master-dev-only`. Do not set it from `.env`'s staging `POSTGRES_PASSWORD`.

## Pre-conditions
- `sre-host-infra` stack is running on `DEV_DOCKER_HOST` (Traefik + `sre-traefik-public` network).
- If unsure, run: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p sre-host-infra ps`

## Steps

1. Compute `BRANCH_SLUG=$(echo <branch> | tr '/_.' '-' | tr '[:upper:]' '[:lower:]')`
2. Check if already up: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose ls --filter name=$BRANCH_SLUG`
   - If non-empty, print the existing env info and stop (not a failure).
3. Run:
   ```
   just dev-up <branch>
   ```
   The recipe builds the dev stage from the current commit and brings up the branch-slug compose project: `master`, `mosquitto`, and a bundled `postgres` (profile `db-bundled`, forced on for dev; ignores `DATABASE_URL`/`COMPOSE_PROFILES` in `.env`). Each branch gets its own `postgres_data` volume and `internal` network (project-scoped). master waits for postgres to be healthy, auto-migrates the empty DB, then the recipe polls the master healthcheck and prints the Traefik URL.

## Database access (include in the answer when asked)
- psql: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $BRANCH_SLUG exec postgres psql -U master -d master`
- Schema check: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $BRANCH_SLUG exec master bash scripts/db.sh verify-schema`
- Snapshot/migrate: see `db-snapshot-migrate.md`.

## On failure
- Non-zero exit: stop and escalate. Do not retry without escalation.
- Healthcheck timeout: the recipe already prints the last 50 lines of master logs. Pass through and escalate.
- Logs showing `refusing to start` or `schema is behind/ahead`: pass through verbatim and escalate; do not delete volumes to "fix" it.

## Output
The recipe's stdout is the user-facing output. Pass through verbatim.
