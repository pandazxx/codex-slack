# Operation: Migrate the singleton database before a deploy

## Trigger condition
User explicitly asks to migrate the staging or prod database for a release, or `just deploy` printed `→ run: just migrate <env> <tag>` (master logs `DB schema is behind code`). Staging and prod operations need the user's explicit request.

## Inputs
- `<env>` — `staging` or `prod`
- `<tag>` — the image tag about to be deployed, e.g. `v5.1-rc1`. The migration runs from this image.
- prod only: the user's explicit confirmation, passed as `confirm=yes`. Never add it on your own; without it the recipe refuses and changes nothing.

## Required env vars
- `REGISTRY`
- `STAGING_DOCKER_HOST` (staging) or `PROD_DOCKER_HOST` (prod)
- Database, in `.env` or the shell: `COMPOSE_PROFILES=db-bundled` + `POSTGRES_PASSWORD`, or `DATABASE_URL`

## Pre-conditions
- The image `${REGISTRY}/codex-slack-master:<tag>` exists in the registry.
- Master may be down or crash-looping; the recipe stops it if running.

## Steps
1. Run `just migrate <env> <tag>` (prod: `just migrate prod <tag> confirm=yes`). It pulls the image, ensures bundled Postgres is healthy, prints the revision, stops master, runs `snapshot-and-migrate` in a one-off container, prints the revision after and the snapshot directory. It never starts master.
   Already at the image head: exits 0 and leaves master running. DB ahead of the image or at an unknown revision: refuses with exit 1 and changes nothing.
2. Success ends with `Next: just deploy <env> <tag>`. Run that only if the user asked for the deploy.

## On failure
- Non-zero exit: stop and escalate with the full output. Do not retry on your own. If master was stopped, the recipe prints recovery options (re-run, restore snapshot, redeploy previous tag). Never delete `postgres_data` or `master_data`.
- Do not use `scripts/db.sh verify-schema`; it always fails (pandazxx/codex-slack#283).

## Output
The script's stdout is the user-facing output. Pass through verbatim.
