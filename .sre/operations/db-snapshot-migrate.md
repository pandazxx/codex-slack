# Operation: Snapshot and migrate the database (running master)

Use when master logs `DB schema is behind code` (after an upgrade) or the user asks for a snapshot or provider migration on a running stack. Not for the one-time staging SQLite cut-over: that is in `deploy.md`.

## Inputs
- `<env>` — `dev`, `staging` or `prod`
- `<branch>` — branch name (dev only)

## Required env vars
- `DEV_DOCKER_HOST` (dev), or `STAGING_DOCKER_HOST` / `PROD_DOCKER_HOST` and `REGISTRY` (singleton)

## Pre-conditions
- Dev: the master container is running (`just status`).
- Staging and prod: master may be crash-looping on "schema is behind"; `just migrate` handles that.
- For staging and prod, the user has explicitly asked for this operation.

## Steps

1. Dev. Compute `BRANCH_SLUG` (`tr '/_.' '-' | tr '[:upper:]' '[:lower:]'`), then run:
   ```
   DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $BRANCH_SLUG -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.postgres.yml exec -T master bash -c 'bash scripts/db.sh snapshot-and-migrate --source-url "${DATABASE_URL:-postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master}"'
   ```
2. Staging or prod. Run `just migrate <env> <tag>` (prod: `just migrate prod <tag> confirm=yes`, only when the user explicitly confirmed), where `<tag>` is the tag about to be deployed. It runs this same `snapshot-and-migrate` from the target tag's image in a one-off container, stops master first, and prints the revision before and after plus the snapshot directory. Success ends with `Next: just deploy <env> <tag>`. Details: `.sre/operations/migrate.md`.
   Manual fallback (only if the recipe is unavailable; same effect, no revision checks):
   ```
   DOCKER_HOST=$STAGING_DOCKER_HOST MASTER_RUNTIME_IMAGE=<registry>/codex-slack-master:<tag> IMAGE_DIGEST=<sha256 of that tag> docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.postgres.yml run --rm --no-deps -T master bash -c 'bash scripts/db.sh snapshot-and-migrate --source-url "${DATABASE_URL:-postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master}"'
   ```
   Use the target tag's image (it has the new revisions), with master stopped first. Success: output contains `Migration complete.` The snapshot is written to `/opt/codex-slack/data/master/snapshots/<UTC timestamp>/` inside the `master_data` volume.
3. Restart master so it re-checks the schema: `just dev-up <branch>` (dev) or `just deploy <env> <tag>` with the tag that was just migrated (staging, prod).
4. Verify: `just migrate` prints the revision after. Do not use `scripts/db.sh verify-schema`; it always exits 1 (pandazxx/codex-slack#283). Dev: `DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $BRANCH_SLUG -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.postgres.yml exec -T postgres psql -U master -d master -c 'select version_num from alembic_version'`.

## On failure
- Non-zero exit: stop and escalate with the full output. Do not retry, and never delete `postgres_data` or `master_data`.

## Output
Pass the command output through verbatim, then state the snapshot directory.
