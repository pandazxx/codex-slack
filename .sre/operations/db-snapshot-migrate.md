# Operation: Snapshot and migrate the database (running master)

Use when master logs `DB schema is behind code` (after an upgrade) or the user asks for a snapshot or provider migration on a running stack. Not for the one-time staging SQLite cut-over: that is in `deploy.md`.

## Inputs
- `<env>` — `dev` or `staging`
- `<branch>` — branch name (dev only)

## Required env vars
- `DEV_DOCKER_HOST` (dev) or `STAGING_DOCKER_HOST` (staging)

## Pre-conditions
- The master container is running (`just status`). If it is crash-looping on "schema is behind", use the `run --rm --no-deps -T` form in step 2 instead of `exec -T`.
- For staging, the user has explicitly asked for this operation.

## Steps

1. Dev. Compute `BRANCH_SLUG` (`tr '/_.' '-' | tr '[:upper:]' '[:lower:]'`), then run:
   ```
   DOCKER_HOST=$DEV_DOCKER_HOST docker compose -p $BRANCH_SLUG -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.postgres.yml exec -T master bash -c 'bash scripts/db.sh snapshot-and-migrate --source-url "${DATABASE_URL:-postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master}"'
   ```
2. Staging. Run:
   ```
   DOCKER_HOST=$STAGING_DOCKER_HOST MASTER_RUNTIME_IMAGE=placeholder IMAGE_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000 docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.postgres.yml exec -T master bash -c 'bash scripts/db.sh snapshot-and-migrate --source-url "${DATABASE_URL:-postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master}"'
   ```
   Success: output ends with `Migration complete.` The snapshot is written to `/opt/codex-slack/data/master/snapshots/<UTC timestamp>/` inside the `master_data` volume.
3. Restart master so it re-checks the schema: `just dev-up <branch>` (dev) or `just deploy staging <tag>` with the tag currently deployed (staging).
4. Verify: repeat the step 1 or 2 command, replacing the quoted `bash -c` payload with `'bash scripts/db.sh verify-schema'`. Success: exit 0.

## On failure
- Non-zero exit: stop and escalate with the full output. Do not retry, and never delete `postgres_data` or `master_data`.

## Output
Pass the command output through verbatim, then state the snapshot directory.
