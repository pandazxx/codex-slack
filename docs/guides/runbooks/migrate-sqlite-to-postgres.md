# Runbook: Migrate SQLite to Postgres

## Trigger condition

Master prints the following message to stderr and exits with code 1:

```
ERROR: existing SQLite data detected at
  /opt/codex-slack/data/master/master_data.db
but DATABASE_URL is unset and the default is now Postgres. Pick one:

  (a) Keep SQLite:
      export DATABASE_URL=sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db

  (b) Migrate to Postgres (recommended):
      scripts/db.sh snapshot-and-migrate \
          --source-url "sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db" \
          --target-url "$POSTGRES_URL"
      Then set DATABASE_URL=$POSTGRES_URL.

Set DATABASE_URL to suppress this check.
See docs/guides/runbooks/migrate-sqlite-to-postgres.md
```

This check fires when `DATABASE_URL` is unset and a non-empty `master_data.db` with a `workspaces` table exists under `MASTER_DATA_DIR`. It means you have upgraded from a pre-v5.0 release whose default storage was SQLite to v5.0, whose default is Postgres.

## Impact

Master will not start until you choose one of the options below. All existing data remains in the SQLite file; nothing has been modified yet.

## Prerequisites

- Docker Compose >= 2.20 (required for `depends_on.required: false` in `docker-compose.postgres.yml`)
- Access to the machine running the stack and to the repo root `.env` file
- For the Postgres paths: ability to write to `.env`
- For SRE-managed staging/prod deploys: see `.sre/operations/deploy.md` (section "Staging cut-over: SQLite to bundled Postgres") — it is the authoritative SRE procedure. Return here for the self-hosted `docker compose` path.

> **Compose project name required.** Every `docker compose` command below needs
> `-p <project>` to resolve the same named project (and therefore the same named
> volumes) as the running stack.  Without it Docker Compose defaults to a project
> name derived from the current directory, and `run` would mount a brand-new empty
> volume instead of the live data volume.
>
> Find the project name:
> ```bash
> docker compose ls               # shows "codex-slack" or whatever name is in use
> docker volume ls | grep master_data   # e.g. codex-slack_master_data
> ```
> The project name is the prefix before `_master_data`.  Use that value in place
> of `<project>` throughout this runbook.

---

## Option (a) — Keep SQLite (no migration, zero downtime)

If you want to continue using the existing SQLite database without migrating:

1. Add or set in `.env` (or in the container environment):

   ```
   DATABASE_URL=sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db
   ```

   The four slashes are correct: three for the URL scheme prefix and one for the absolute path.

2. Start master normally. The safety-check is suppressed when `DATABASE_URL` is set.

SQLite remains supported and receives the same automated test coverage as Postgres. Choose this path when you prefer operational simplicity over Postgres features.

---

## Option (b) — Migrate to Postgres

### Step 1 — Back up the data volume (required before any migration)

From the machine that has Docker access to the host:

```bash
# Streaming tar of the master_data volume to a local .tgz
docker run --rm \
  -v codex-slack_master_data:/data:ro \
  alpine tar czf - -C /data . > "master_data-pre-pg-$(date +%Y%m%d%H%M).tgz"

# Verify: confirm master_data.db is listed
tar tzf master_data-pre-pg-*.tgz | grep master_data.db
```

Keep this file until you have verified the migration and run on Postgres for at least one release cycle.

> **Note:** The migration tool (`snapshot-and-migrate`) never writes to the source SQLite file. The file remains untouched and is the rollback artifact.

### Step 2 — Choose a Postgres mode

#### Mode B1 — Bundled Postgres (recommended for most self-hosted setups)

Add to `.env`:

```
COMPOSE_PROFILES=db-bundled
POSTGRES_PASSWORD=<generate: openssl rand -hex 24>
```

Use a hex password (letters and digits only): master splices it into the connection URL without URL-encoding.

Start the Postgres service and wait for it to be ready:

```bash
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml up -d postgres
# Wait for healthcheck (pg_isready)
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml \
  exec postgres pg_isready -U master -d master
```

#### Mode B2 — Managed (external) Postgres

Set in `.env` or in your shell:

```bash
export DATABASE_URL="postgresql+psycopg://user:password@db.example.com:5432/dbname"
```

Make sure the database exists and the user has `CREATE TABLE` privileges. Skip to Step 3.

### Step 3 — Stop master (important — do not skip)

Master must not be running when you import into a non-empty Postgres target. If it is, the import tool attempts to acquire `ACCESS EXCLUSIVE` table locks and will fail with:

```
ERROR: target database is in use (lock timeout after 30s) — stop master before importing; see docs/guides/runbooks/migrate-sqlite-to-postgres.md
```

If master is already failing to start (the trigger condition above), it is not running and this step is a no-op. If you are re-running an import after a partial failure:

```bash
# Bundled path
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml stop master

# Or with justfile
just undeploy staging   # (staging) — preserves volumes
```

### Step 4 — Run snapshot-and-migrate

Set these shell variables (adjust paths and credentials to match your setup):

```bash
SQLITE_URL='sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db'
PGPW="${POSTGRES_PASSWORD}"   # from .env or your shell
PGURL="postgresql+psycopg://master:${PGPW}@postgres:5432/master"
```

> **How `docker compose run` sees the SQLite file.**
> `docker compose run --rm --no-deps` creates a one-off container from the
> `master` service definition, which includes all of the service's declared
> volumes.  The `master_data` volume is mounted at `/opt/codex-slack/data/master`
> inside the container, exactly where `master_data.db` lives.  No extra `-v`
> flag is needed.  This works only when the command resolves the **same Compose
> project** as the running stack — hence the `-p <project>` flag on every
> command below.

#### Pre-flight — confirm the SQLite file is visible

Before running the migration, verify the file is accessible inside the container:

```bash
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml \
  run --rm --no-deps \
  -e "DATABASE_URL=${SQLITE_URL}" \
  master ls -lh /opt/codex-slack/data/master/master_data.db
```

Expected: a non-zero file size.  If the file is not listed, the project name is
wrong or the volume does not contain the database.  Stop and check with
`docker volume ls | grep master_data`.

#### Pre-baseline source (if applicable)

The migration tool requires the source database to be fully up-to-date at the
current Alembic schema before it can export.  If the source SQLite is on an older
schema (pre-v5.0, before Alembic was introduced — recognisable by the `workspaces`
table existing but no `alembic_version` table), you must bring it to the current
schema first.

Two equivalent approaches (use whichever fits your workflow):

**Approach A — `db.sh migrate` (direct, recommended for this runbook):**

```bash
# Take a fresh backup first (step 1 above should already cover this)
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml \
  run --rm --no-deps \
  -e "DATABASE_URL=${SQLITE_URL}" \
  master bash scripts/db.sh migrate
```

**Approach B — start master on SQLite once** (master's `startup_db` applies the
same fixups on startup and stamps the baseline revision):
```bash
# This is the approach used in .sre/operations/deploy.md step 3.
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml \
  -e "DATABASE_URL=${SQLITE_URL}" up -d master
# Wait for master to report healthy, then stop it before running snapshot-and-migrate.
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml stop master
```

After either approach the source will be at the current head and the export will proceed.

#### Bundled Postgres (Mode B1)

Use `docker compose run --rm --no-deps` to run the migration tool in a one-off master container. Postgres must already be running (Step 2).

```bash
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml \
  run --rm --no-deps \
  -e "DATABASE_URL=${SQLITE_URL}" \
  master bash scripts/db.sh snapshot-and-migrate \
    --source-url "${SQLITE_URL}" \
    --target-url "${PGURL}"
```

The tool prints an export summary (per-table row counts) and an import summary.
Note these counts for the verification step.

Expected last line: `Provider migration complete.`

The snapshot is written inside the container to `${MASTER_DATA_DIR}/snapshots/<UTC timestamp>/` (inside the `master_data` volume). For the staging path with the justfile, see `.sre/operations/db-snapshot-migrate.md` for the exact `exec` command.

#### Managed Postgres (Mode B2)

The `--source-url` points at the SQLite file (inside the master container's data
volume); the target is your external Postgres URL.  Use a distinctly named
variable (`TARGET_URL`) to avoid confusion with `DATABASE_URL`, which is
overridden to the SQLite path in the same command:

```bash
TARGET_URL="postgresql+psycopg://user:password@db.example.com:5432/dbname"

docker compose -p <project> -f docker-compose.yml \
  run --rm --no-deps \
  -e "DATABASE_URL=${SQLITE_URL}" \
  master bash scripts/db.sh snapshot-and-migrate \
    --source-url "${SQLITE_URL}" \
    --target-url "${TARGET_URL}"
```

The tool prints an export summary and an import summary.  Note these counts for
the verification step.

> **Cross-release snapshots are not supported.** The snapshot schema version must
> match the Alembic head of the running code.  If the source SQLite needs
> upgrading, run `scripts/db.sh migrate` (or start master on SQLite once, as
> described in the "Pre-baseline source" section above) before running
> snapshot-and-migrate.

### Step 5 — Point DATABASE_URL at Postgres

For bundled Postgres, update `.env`:

```
# Replace or add this line (the SQLite file is still present but DATABASE_URL is explicit now)
DATABASE_URL=postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master
```

For managed Postgres, `DATABASE_URL` should already be set from Step 2.

### Step 6 — Start master

```bash
# Bundled path
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml up -d

# Or with justfile
just deploy staging <tag>
```

Master runs `startup_db()` on start. Because the DB has already been migrated and stamped at the current Alembic head, it finds the schema up-to-date and proceeds.

---

## Verification

```bash
# Check schema matches code head
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml \
  exec master bash scripts/db.sh verify-schema

# Check row counts on key tables
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml \
  exec postgres psql -U master -d master \
  -c 'select count(*) from workspaces; select count(*) from messages;'
```

`verify-schema` exits 0 on success and prints `schema is up to date`.

**Row-count comparison:** The export step printed an "Export summary" with per-table row
counts, and the import step printed an "Import summary".  Both totals must agree exactly.
If the counts differ, stop — do not start master — and check the full output for error
messages before retrying.

Compare the workspace and message counts against what you recall (or against the
pre-migration volume backup you inspected earlier).

Open the UI and confirm workspaces, topics, and recent messages are visible.

---

## Rollback

The SQLite file is never modified by the migration tool. To revert:

1. Stop master.
2. Set in `.env`:
   ```
   DATABASE_URL=sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db
   ```
3. Remove or comment out `COMPOSE_PROFILES=db-bundled` (optional — Postgres will start but master won't use it).
4. Start master.

Any data written to Postgres after the migration is lost on rollback. The Postgres `postgres_data` volume is preserved by `just undeploy`; `just destroy` removes it permanently.

To reset a failed Postgres import and retry from Step 4:

```bash
# Stop and remove the postgres container, then delete its volume
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml stop postgres
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml rm -f postgres
docker volume rm <project>_postgres_data

# Re-create the volume by starting postgres fresh
docker compose -p <project> -f docker-compose.yml -f docker-compose.postgres.yml up -d postgres
```

---

## Escalation

If the migration exits non-zero, stop immediately. Do not retry without reading the full error output. Do not delete `master_data` or `postgres_data`. Provide the full output to the project maintainers.

Authoritative SRE runbooks:
- Staging cut-over: `.sre/operations/deploy.md` § "Staging cut-over: SQLite to bundled Postgres"
- Schema behind / snapshot on a running stack: `.sre/operations/db-snapshot-migrate.md`
