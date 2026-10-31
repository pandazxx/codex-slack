# Operation: Deploy (or upgrade) singleton stack

## Trigger condition
User asks to deploy a tagged image to staging or prod, e.g. "deploy v4.19-rc1 to staging".

## Inputs
- `<env>` — target environment: `staging` or `prod`
- `<tag>` — CI-built image tag, e.g. `master`, `v4.19-rc1`, `v4.19`

## Required env vars
- `REGISTRY` — image namespace, e.g. `ghcr.io/pandazxx`
- `STAGING_DOCKER_HOST` — required when `env=staging`, e.g. `ssh://ubuntu@10.10.10.227`
- `PROD_DOCKER_HOST` — required when `env=prod`, e.g. `ssh://ubuntu@prod.example.com`
- Database, in `.env` or the shell. Exactly one mode:
  - bundled Postgres: `COMPOSE_PROFILES=db-bundled` and `POSTGRES_PASSWORD` (hex, from `openssl rand -hex 24`)
  - managed DB or SQLite: `DATABASE_URL` set, `COMPOSE_PROFILES` unset

## Pre-conditions
- The image `${REGISTRY}/codex-slack-master:<tag>` exists in the registry and is pullable from the target host.
- The operator's SSH key is loaded and can reach the target host.
- A database mode above is configured. The recipe refuses to run otherwise (nothing is touched on the host).

## Steps

1. Run:
   ```
   just deploy <env> <tag>
   ```
   The recipe:
   - Validates the database configuration, and refuses if `master_data` still holds a legacy `master_data.db` while `DATABASE_URL` is empty (master would crash-loop).
   - Resolves `${REGISTRY}/codex-slack-master:<tag>` to an immutable `sha256:...` digest and pulls it on the target host.
   - Runs `docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.postgres.yml up -d --remove-orphans`. The fixed project name replaces a running stack in place. With `db-bundled`, `postgres` starts first (healthcheck `pg_isready`) and persists in the `postgres_data` volume; master auto-migrates an empty database and refuses to start on schema drift.
   - Polls `http://<host>:${MASTER_PORT:-8080}/health` up to 90 s, then prints the image reference, URL and database mode.

2. Verify: `curl -sf http://<host>:${MASTER_PORT:-8080}/health`

## On failure
- `no database configured` / `POSTGRES_PASSWORD is empty`: the host is not configured for this release. Stop and escalate to the user; point to "Staging cut-over" below. Do not edit `.env` yourself.
- `legacy SQLite database`: same. Stop and escalate. Do NOT run the cut-over unless the user explicitly asks for it.
- Digest resolution failure ("no manifest"): the tag does not exist yet. Confirm the CI build completed, then retry.
- Healthcheck timeout: the recipe prints the last 50 lines of master logs. Pass them through and escalate; do not retry without investigation. Master log lines `refusing to start` or `schema is behind` mean a database state problem, not a transient one.

## Rollback
`just deploy <env> <previous-tag>`. This does not roll back database contents. Never delete `postgres_data` to roll back.

## Staging cut-over: SQLite to bundled Postgres (once per host, human-run)

Needed when the host's `master_data` volume holds `master_data.db` and the release is the first with Postgres default. Run from the repo root on the machine that has `.env`, with `STAGING_DOCKER_HOST` and `REGISTRY` set. Expect a few minutes of staging downtime. The SQLite file is never modified after step 3 and stays as the rollback artifact.

```
# 0. Variables and helper
TAG=master                    # or the RC tag, e.g. v4.20-rc1
SQLITE_URL='sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db'
dcs() { DOCKER_HOST="$STAGING_DOCKER_HOST" COMPOSE_PROFILES=db-bundled MASTER_RUNTIME_IMAGE=placeholder IMAGE_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000 docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.postgres.yml "$@"; }

# 1. Back up the data volume to this machine, then check it is non-empty and readable
DOCKER_HOST="$STAGING_DOCKER_HOST" docker run --rm -v codex-slack_master_data:/data:ro alpine tar czf - -C /data . > "master_data-pre-pg-$(date +%Y%m%d%H%M).tgz"
ls -lh master_data-pre-pg-*.tgz && tar tzf master_data-pre-pg-*.tgz | grep master_data.db

# 2. Configure .env — abort if any of these keys already exist (duplicate keys cause
#    the first occurrence to win, silently ignoring the new value).
existing=$(grep -cE '^(COMPOSE_PROFILES|POSTGRES_PASSWORD|DATABASE_URL)=' .env 2>/dev/null || true)
if [ "$existing" -gt 0 ]; then
  echo "ERROR: .env already contains COMPOSE_PROFILES, POSTGRES_PASSWORD, or DATABASE_URL."
  echo "Edit those entries in place instead of appending.  Aborting."
  exit 1
fi
PGPW="$(openssl rand -hex 24)"
printf '\nCOMPOSE_PROFILES=db-bundled\nPOSTGRES_PASSWORD=%s\nDATABASE_URL=%s\n' "$PGPW" "$SQLITE_URL" >> .env

# 3. Deploy: starts an empty postgres, and master on SQLite (brings legacy SQLite to the current schema in place)
just deploy staging "$TAG"

# 4. Stop master, then copy SQLite into Postgres with a one-off container
( set -a; . ./.env; set +a
  export DOCKER_HOST="$STAGING_DOCKER_HOST"
  export MASTER_RUNTIME_IMAGE="$REGISTRY/codex-slack-master:$TAG"
  export IMAGE_DIGEST="$(docker buildx imagetools inspect --format '{{json .Manifest.Digest}}' "$MASTER_RUNTIME_IMAGE" | tr -d '"')"
  dcr() { docker compose -p codex-slack -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.postgres.yml "$@"; }
  dcr stop master &&
  dcr run --rm --no-deps -T master bash scripts/db.sh snapshot-and-migrate \
      --source-url "$SQLITE_URL" \
      --target-url "postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master" )
# Expected last line: "Provider migration complete."  If it fails, go to Rollback below.

# 5. Point master at Postgres explicitly (required: the SQLite file is still in the volume)
sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://master:${PGPW}@postgres:5432/master|" .env && rm .env.bak

# 6. Start master on Postgres
just deploy staging "$TAG"

# 7. Verify
dcs exec -T master bash scripts/db.sh verify-schema
dcs exec -T postgres psql -U master -d master -c 'select count(*) from workspaces;'
```

Rollback (SQLite data is untouched; Postgres data is discarded):
```
sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=${SQLITE_URL}|" .env && rm .env.bak
just deploy staging "$TAG"
```
If the import failed part-way and you want to retry step 4, first reset the empty target: `dcs stop postgres && dcs rm -f postgres && DOCKER_HOST="$STAGING_DOCKER_HOST" docker volume rm codex-slack_postgres_data && dcs up -d --wait postgres`. If `master_data` itself was damaged, `just undeploy staging`, then restore: `DOCKER_HOST="$STAGING_DOCKER_HOST" docker run --rm -i -v codex-slack_master_data:/data alpine sh -c 'cd /data && tar xzf -' < master_data-pre-pg-<stamp>.tgz`.
After one release cycle on Postgres, the user may remove `master_data.db` from the volume.

## Notes
- One singleton per host; only one deploy occupies `staging` at a time (RC UAT serializes).
- `MASTER_PORT` defaults to `8080`; `DOCKER_GID` is probed from the target host if unset.
- `prod` works identically once `PROD_DOCKER_HOST` is set.
- Switching from `db-bundled` to a managed `DATABASE_URL` removes the `postgres` container on the next deploy (`--remove-orphans`) but keeps `postgres_data`.
