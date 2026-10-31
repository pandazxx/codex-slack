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

---

## Running the migration with plain `docker run` (managed Postgres)

Use this procedure when you have a managed (external) Postgres target and cannot use `docker compose run` — for example, when running the migration on a machine that has Docker but no Compose stack checked out.

Use the **same image tag** you will deploy to master. The snapshot schema version must match the running code exactly.

### 1. Prepare a working directory

Copy the SQLite file into a dedicated directory. The container runs as uid 1000; the directory must be writable by that uid.

```bash
mkdir -p migration-work
# Copy the DB file (see "Running on a different machine" for hot-copy details)
cp /path/to/master_data.db migration-work/
chown -R 1000:1000 migration-work
```

### 2. Create the env file

```
DATABASE_URL=sqlite+pysqlite:////src/master_data.db
TARGET_URL=postgresql+psycopg://user:password@db.example.com:5432/dbname
```

Save as `migration.env` and restrict access:

```bash
chmod 600 migration.env
```

> **Env file format rules.** `docker run --env-file` reads values literally: no quotes, no `export`, no spaces around `=`. A quoted value such as `TARGET_URL="postgresql://..."` passes the literal quote characters into SQLAlchemy and causes `ArgumentError: Could not parse SQLAlchemy URL from given URL string`. See the Troubleshooting section for a diagnostic one-liner.

> **Password characters.** Letters, digits, and dashes are safe in a URL password. Characters such as `@ : / # % ?` must be percent-encoded (`%40`, `%3A`, etc.).

### 3. Pull the image

```bash
# If the registry is private:
docker login ghcr.io
docker pull ghcr.io/pandazxx/codex-slack-master:<tag>
```

### 4. Check connectivity to the target Postgres

```bash
docker run --rm --env-file migration.env \
  ghcr.io/pandazxx/codex-slack-master:<tag> \
  python -c "import os,sqlalchemy; e=sqlalchemy.create_engine(os.environ['TARGET_URL']); e.connect().close(); print('ok')"
```

Expected output: `ok`. If the connection fails, fix the host, port, firewall, or `pg_hba.conf` before proceeding.

> **Postgres on the same Docker host.** If Postgres runs directly on the host machine (not in a container), use `host.docker.internal` as the hostname in `TARGET_URL` and add `--add-host=host.docker.internal:host-gateway` to the `docker run` call. Alternatively use `--network host` and `localhost` in the URL.

### 5. Migrate the SQLite copy to the current schema

Bring the copy to the current Alembic head before exporting. This modifies the copy, not the original file.

```bash
docker run --rm \
  -v "$(pwd)/migration-work":/src \
  --env-file migration.env \
  ghcr.io/pandazxx/codex-slack-master:<tag> \
  bash scripts/db.sh migrate
```

### 6. Run snapshot-and-migrate

```bash
docker run --rm \
  -v "$(pwd)/migration-work":/src \
  --env-file migration.env \
  ghcr.io/pandazxx/codex-slack-master:<tag> \
  bash -c 'bash scripts/db.sh snapshot-and-migrate \
    --source-url "$DATABASE_URL" \
    --target-url "$TARGET_URL" \
    --out /src/snapshot'
```

The outer single quotes prevent shell expansion on the host; `$DATABASE_URL` and `$TARGET_URL` are expanded inside the container from the env file values.

The tool prints an Export summary and an Import summary. Both totals must agree exactly. Note them for the verification step (see Verification above).

Expected last line: `Provider migration complete.`

The snapshot lands at `migration-work/snapshot/` on the host. Keep it as an audit artefact.

### 7. Delete the env file

```bash
rm migration.env
```

### 8. Point `DATABASE_URL` at Postgres and deploy

Update `.env` on the Docker host:

```
DATABASE_URL=postgresql+psycopg://user:password@db.example.com:5432/dbname
```

Leave `COMPOSE_PROFILES` unset (no bundled Postgres needed). Then deploy as normal (Step 6 of Option (b)).

---

## Running the migration on a different machine than the Docker host

### When this applies

- The target is a managed (external) Postgres reachable from the migration machine (firewall open, `pg_hba.conf` allowing that machine's IP).
- The migration machine has the **same release** as the master you will deploy; the snapshot schema version must match.
- This approach does **not** work with bundled Postgres — the bundled `postgres` service has no published port and is only reachable on the Compose project network. For bundled Postgres, run the migration on the Docker host using the B1 steps in Option (b).

> **`DOCKER_HOST` trap.** When `DOCKER_HOST=ssh://...` is set on a laptop, `docker` commands and `-v /path` bind mounts resolve on the **remote** host's filesystem. Always confirm which machine `docker` is talking to before running volume-mount commands.

### Step 1 — Get a consistent copy of the SQLite DB

Choose one method.

**Method A — Hot copy (master running).**

Use the SQLite backup API inside the running container. The backup API produces a consistent copy even while master is writing.

```bash
# Run on the Docker host (or with DOCKER_HOST pointing at it):
docker exec <master-container> python -c "
import sqlite3
s = sqlite3.connect('/opt/codex-slack/data/master/master_data.db')
d = sqlite3.connect('/opt/codex-slack/data/master/master_data.migrate.db')
s.backup(d)
print(d.execute('PRAGMA integrity_check').fetchone()[0])
"
docker cp <master-container>:/opt/codex-slack/data/master/master_data.migrate.db ./
```

Expected output from `PRAGMA integrity_check`: `ok`.

**Method B — Cold copy (master stopped).**

```bash
docker compose -p <project> stop master

docker run --rm \
  -v <project>_master_data:/data \
  -v "$(pwd)":/out \
  alpine \
  cp /data/master/master_data.db /out/master_data.db
```

**Verify the checksum on both ends** after transferring the file.

```bash
# On the Docker host:
sha256sum master_data.db      # or master_data.migrate.db

# After scp to the migration machine:
sha256sum master_data.db
```

The hashes must match. If they differ, the transfer was corrupted; retry.

```bash
scp ubuntu@<docker-host>:~/master_data.db ./
```

Never edit or overwrite the original DB file on the Docker host — work only on the copy.

### Step 2 — Run the migration

**Option A — The migration machine has Docker.**

Ensure `DOCKER_HOST` is unset so `docker` commands and bind mounts resolve locally:

```bash
unset DOCKER_HOST
docker context use default
```

Then follow the full **"Running the migration with plain `docker run`"** procedure above, using the copied file in place of the DB extracted from the volume.

**Option B — The migration machine has no Docker.**

Clone the repo at the exact same tag that will be deployed:

```bash
git clone https://github.com/pandazxx/codex-slack.git codex-slack
cd codex-slack
git checkout <same-tag>
```

Set up a Python 3.11 venv:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Migrate the SQLite copy to the current schema:

```bash
DATABASE_URL='sqlite+pysqlite:////abs/path/to/master_data.db' bash scripts/db.sh migrate
```

> The four slashes are required: `sqlite+pysqlite://` (scheme) + `//abs/path` (absolute path starting with `/`).

Run snapshot-and-migrate:

```bash
export TARGET_URL='postgresql+psycopg://user:password@db.example.com:5432/dbname'
bash scripts/db.sh snapshot-and-migrate \
  --source-url 'sqlite+pysqlite:////abs/path/to/master_data.db' \
  --target-url "$TARGET_URL" \
  --out ./snapshot
```

> When using the shell directly (not `--env-file`), `export` and shell quoting are fine. The no-quotes rule applies only to `docker run --env-file` because that interface reads values literally.

Note the Export summary and Import summary row counts. Both totals must agree exactly. Expected last line: `Provider migration complete.`

### Step 3 — Cut over on the Docker host

Update `.env` on the Docker host:

```
DATABASE_URL=postgresql+psycopg://user:password@db.example.com:5432/dbname
```

Leave `COMPOSE_PROFILES` unset. The legacy `master_data.db` file remaining in the `master_data` volume is harmless because `DATABASE_URL` is now explicit.

Deploy as normal (Step 6 of Option (b)). Keep the `snapshot/` directory on the migration machine until migration is verified and master has run on Postgres for at least one release cycle.

---

## Troubleshooting

### `Could not parse SQLAlchemy URL from given URL string`

**Cause.** The URL value contains unexpected characters injected by the env file format. Common culprits when using `docker run --env-file`:

- Quoted value: `TARGET_URL="postgresql://..."` — the literal `"` character is included.
- Leading space: `TARGET_URL= postgresql://...`
- `export` keyword: `export TARGET_URL=postgresql://...`
- Empty value: `TARGET_URL=`

**Diagnostic.** Print the first 22 characters and total length of the value without revealing credentials:

```bash
docker run --rm --env-file migration.env \
  ghcr.io/pandazxx/codex-slack-master:<tag> \
  python -c "import os; v=os.environ.get('TARGET_URL'); print(repr(v[:22]) if v else repr(v), len(v or ''))"
```

A healthy value starts with `'postgresql+psycopg://'` (21 chars). A quoted value starts with `'"postgresql'` and has a `"` at position 0.

**Fix.** Edit the env file so every line is bare `KEY=value` with no quotes, no `export`, no spaces around `=`, and no blank value.

### `password authentication failed for user ...`

**Step 1 — Verify the password hash** without printing it in clear text. Run inside the migration container:

```bash
docker run --rm --env-file migration.env \
  ghcr.io/pandazxx/codex-slack-master:<tag> \
  python -c "
import os, hashlib
from sqlalchemy.engine import make_url
p = make_url(os.environ['TARGET_URL']).password or ''
print(len(p), hashlib.sha256(p.encode()).hexdigest()[:8])
"
```

Compare the 8-char hash prefix against the expected password on the machine that provisioned it:

```bash
printf '%s' 'your-actual-password' | sha256sum | cut -c1-8
```

If the lengths or prefixes differ, the password in the env file does not match the server's record.

**Step 2 — Interactive psql test.** Isolates whether the password itself is correct from URL-encoding and env-file formatting:

```bash
docker run --rm -it postgres:17-alpine \
  psql "postgresql://user@host:port/db"
```

Enter the password interactively when prompted.

**Step 3 — Server-side check.** Inspect the Postgres log for `role "..." does not exist` or `role "..." does not have a valid SCRAM secret`. Reset the password if needed:

```sql
ALTER ROLE <user> WITH LOGIN PASSWORD 'new-password';
```

### Permission errors when creating tables (Postgres 15+)

On Postgres 15 and later the `public` schema is no longer world-writable. The error is typically `ERROR: permission denied for schema public`.

```sql
-- Option 1: make the migration user the database owner
ALTER DATABASE <dbname> OWNER TO <user>;

-- Option 2: grant schema access only
GRANT ALL ON SCHEMA public TO <user>;
```

### `database is in use (lock timeout after 30s)`

The import step acquires an `ACCESS EXCLUSIVE` lock on every table. If master is running and holding connections, the lock times out after 30 seconds. Stop master before running the import (Step 3 of Option (b)), then re-run from Step 4. Do not retry the import while master is still connected.
