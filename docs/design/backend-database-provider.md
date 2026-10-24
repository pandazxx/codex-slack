# Design: Backend database provider — Postgres default, SQLite and MySQL supported

**Status:** accepted
**Author:** architect
**Date:** 2026-10-24
**Related ADRs:** [ADR-0019](../decisions/0019-backend-database-provider.md); supersedes the "SQLite deferred migration" note in [ADR-0005 §4](../decisions/0005-v3-system-architecture.md); respects the single-user plaintext-secret posture of [ADR-0009](../decisions/0009-runtime-configuration-and-staff-system.md) and [ADR-0010](../decisions/0010-workspace-env-var-overrides.md); builds on the singleton deploy shape in [ADR-0016](../decisions/0016-singleton-justfile-deploys.md).

## Revision history

- **2026-10-24 (rev 3)** — Design approved by project-owner and
  PR #269 merged. OQ10 resolved as replace-only (the recommendation).
  ADR-0019 accepted.
- **2026-10-24 (rev 2)** — Postgres becomes the default (was: SQLite
  default, Postgres opt-in). All OQs resolved per project-owner
  decisions and code-review findings except OQ10, which is reworded
  and remains open pending confirmation. Added paramstyle choice,
  Docker Compose profile mechanism, SQLite-upgrade safety behaviour,
  sequence-reset handling on snapshot import, MySQL reserved-word /
  VARCHAR-width handling, and a corrected enumeration of modules
  that touch the DB. CI matrix is per-PR for all three dialects.
- **2026-10-24 (rev 1)** — Initial draft paired with ADR-0019.

## Context

The master service persists state in a single SQLite file. Issue
[#268](https://github.com/pandazxx/codex-slack/issues/268) asks for a
pluggable backend (PostgreSQL first-class, MySQL second), plus data
migration tooling that lets existing SQLite installs cut over without
losing history, plus compose changes to run the chosen backend
alongside the stack.

Today's reality (surveyed 2026-10-24, verified against
`src/master/db.py` and all callers):

- `src/master/db.py` holds a ~670-line `_SCHEMA` string, a 28-entry
  `_MIGRATIONS` list of `ALTER TABLE ADD COLUMN` statements executed
  with "swallow OperationalError" semantics, and **eight** migration
  helpers — four of which are full table rebuilds
  (`_migrate_workspace_name_uniqueness`,
  `_migrate_event_actions_v2`, `_migrate_event_actions_v3`,
  `_migrate_staffs_session_scope_none`) to handle
  CHECK-constraint and uniqueness-constraint changes (SQLite cannot
  `ALTER CONSTRAINT`). The other four
  (`_migrate_staged_dispatches`, `_migrate_agents_to_staffs`,
  `_migrate_envelope_indexes`, `_migrate_envelope_backfill`) are
  additive — new table creation, data move, index creation, and
  data backfill respectively.
- **16 production modules** touch the DB (via `sqlite3.connect`
  directly or via `get_connection`): `attachments.py`, `db.py`,
  `dispatch.py`, `event_actions.py`, `event_dispatcher.py`,
  `main.py`, `messages.py`, `mqtt_client.py`, `notes.py`,
  `notify.py`, `orchestrate_api.py`, `runtime_config.py`,
  `staffs.py`, `topic_export.py`, `topics.py`, `workspaces.py`.
  **13 test modules** touch it. Total: 29 files.
- *Direct `sqlite3.connect(db_path)` outside `db.py`* lives in
  `main.py` (4 call sites), `mqtt_client.py` (12 call sites), and
  `notify.py` (2 call sites). These bypass any central pool. **Phase 0
  funnels them through the SQLAlchemy engine's `get_connection()`
  shim**; there will be no `sqlite3.connect()` outside `db.py` and
  the pre-baseline fixup module after Phase 0 lands.
- *All call sites use DB-API qmark `?` placeholders with positional
  tuples.* `text()` requires named `:param` with a dict; psycopg uses
  `%s`; PyMySQL uses `%s`. Phase 0 converts qmark `?` to `text()`
  with named `:param` across every call site — see §3 for the
  paramstyle rationale.
- SQLite-only syntax actually used:
  - `INSERT OR IGNORE` (dispatch.py, mqtt_client.py, main.py, db.py
    migrations)
  - `INSERT OR REPLACE` (zero today — upsert goes through
    `ON CONFLICT ... DO UPDATE SET = excluded.X` in
    `runtime_config.py`, which is already Postgres-compatible)
  - `unixepoch('now','subsec')` default on `chunks.created_at`
  - `PRAGMA foreign_keys = ON/OFF`, `PRAGMA table_info(...)`
  - `sqlite_master` introspection in migration helpers
  - Partial indexes (`WHERE archived_at IS NULL`,
    `WHERE sender_kind IS NULL`, `WHERE dispatched_at IS NULL`,
    `WHERE event_type = 'topic_scheduler'`)
  - Expression unique indexes (`COALESCE(scope_id, '')`)
  - `INTEGER PRIMARY KEY AUTOINCREMENT` on `chunks.id` (the only
    identity column in the schema — every other PK is a UUID TEXT)
  - Table-recreation migration pattern for CHECK-constraint changes
- Datetimes are stored as ISO-8601 TEXT (`YYYY-MM-DDTHH:MM:SSZ`) —
  already dialect-neutral. The one float column
  (`chunks.created_at REAL`) and `tasks.failure_score REAL` are the
  exceptions.
- Reserved-word collisions on MySQL: the `config` and `notes` tables
  both have a `key` column, which MySQL treats as reserved. Phase 1
  quotes these consistently via SQLAlchemy's Core column references
  (backticks on MySQL, double-quotes on Postgres, both handled
  automatically).
- Short-lived connections per request (`get_connection(db_path)`
  called inside each FastAPI handler) is the dominant pattern.
  Background threads open their own. There is no pool.

## Goals

- One configuration surface for picking the DB backend:
  `DATABASE_URL` (SQLAlchemy URL format). **Postgres is the default**;
  SQLite and MySQL are selected by setting `DATABASE_URL` explicitly.
- Postgres is a *first-class* production target: supported end-to-end
  (schema, migrations, deploy, backup, tests, docs, UAT).
- SQLite and MySQL are *supported* alternative targets with full CI
  coverage. MySQL has a documented minimum version floor (MySQL
  8.0.16+) so partial-index / functional-index / enforced-CHECK
  semantics are available.
- Snapshot tooling exports a provider-neutral representation of the
  current DB and imports it into a fresh target DB of any supported
  provider. Import resets identity sequences so subsequent inserts
  do not collide with imported rows.
- CI runs the master test suite against SQLite, Postgres, and MySQL
  on every PR. Postgres is top priority (its failures gate merge
  loudly).
- Operators get a one-page runbook for migrating an existing SQLite
  install to Postgres with downtime bounded by snapshot size.

## Non-Goals

- *Not* switching to async DB. Master mixes sync FastAPI handlers,
  background threads, and `asyncio.to_thread` bridges. Async
  SQLAlchemy across that surface is a separate design.
- *Not* splitting read/write replicas, adding connection-level
  sharding, or supporting multi-master.
- *Not* changing attachment storage (filesystem under
  `MASTER_DATA_DIR/attachments` per ADR-0007). Blobs stay out of the
  DB.
- *Not* adding encryption at rest or TLS to the DB. ADR-0009's
  plaintext posture carries over; plaintext connections to the
  Postgres service (bundled on the same compose network, or on a
  trusted LAN segment when managed externally) are acceptable per
  OQ8.
- *Not* porting the agent worker or the notes MCP server to the new
  provider abstraction. Both are stateless with respect to the
  master DB and talk via MQTT / HTTP.
- *Not* a full-ORM rewrite. ADR-0019 chose SQLAlchemy Core + Alembic
  explicitly to avoid that.

## Proposed Design

### 1. Scope

Resolved from OQ1: this ADR covers the **master DB only** — the
same scope as today's SQLite file. The agent worker and the notes
MCP server do not persist anything in the master DB today and are
untouched.

### 2. Abstraction layer

A new `src/master/db/` package replaces today's `src/master/db.py`
module. The public surface stays small so that existing call sites
change in one mechanical pass:

```python
# today
from .db import get_connection, init_db

# after phase 0
from .db import get_connection, init_db, engine, session
```

- `engine()` returns the module-level SQLAlchemy `Engine` built from
  `DATABASE_URL` with a pool sized by `DB_POOL_SIZE`
  (default 5, SQLite falls back to `StaticPool`).
- `get_connection()` returns a dialect-aware connection object that
  **behaves like** today's `sqlite3.Connection` for the subset of
  methods call sites use (`execute`, `executescript`, `fetchone`,
  `fetchall`, `commit`, `close`, context manager). Row objects are
  mapping-like (`row["id"]`) via `.mappings()`. This shim is the
  smallest-possible change for the sixteen production modules that
  already use `get_connection`.
- Low-level helpers that depend on SQLite-only syntax
  (`PRAGMA foreign_keys`, `sqlite_master` lookups) move into
  dialect-dispatched private helpers (`_dialect.upsert`,
  `_dialect.now_literal`, `_dialect.table_exists`,
  `_dialect.reset_sequence`, etc.).

### 3. Paramstyle: `text()` with named `:param` bindings

All call sites convert from DB-API qmark `?` with positional tuples to
SQLAlchemy `text()` with named `:param` bindings and a dict of
parameters. Example:

```python
# today
conn.execute(
    "INSERT INTO staffs (id, name, created_at) VALUES (?, ?, ?)",
    (sid, name, now),
)

# phase 0
conn.execute(
    text("INSERT INTO staffs (id, name, created_at)"
         " VALUES (:id, :name, :created_at)"),
    {"id": sid, "name": name, "created_at": now},
)
```

Rationale (also recorded in ADR-0019):

- `text()` compiles to the right DB-API paramstyle automatically
  (`?` on pysqlite, `%s` on psycopg and PyMySQL). No runtime
  translator to maintain.
- `exec_driver_sql()` was considered as a shortcut (keep `?` on
  SQLite, rewrite to `%s` elsewhere). Rejected: it bypasses
  SQLAlchemy's type coercion, which matters on Postgres — booleans
  must be real `bool`, timestamps must be `str`/`datetime` depending
  on column type, and the integer PK on `chunks.id` must flow
  through the server-side sequence.
- Named `:param` is easier to read where a call site has many
  parameters. The heaviest site today (`orchestrate_api.py`) has 38
  execute calls, several binding 10+ values.

Phase 0 scope therefore includes a mechanical conversion pass across
16 production files and 13 test files. Automation: a small AST-based
rewrite script (`scripts/convert_qmark_to_named.py`, kept in the PR
and deleted after merge) handles the common shape; a human reviews
the diff file-by-file. **"No behaviour change" in Phase 0 means
"same results, same semantics" — not "same source text."** The ADR
is corrected accordingly.

### 4. Schema and migration management

Alembic owns schema evolution end-to-end. The in-code `_SCHEMA`
string and `_MIGRATIONS` list in `db.py` go away in phase 1.

Layout:

```
src/master/db/
  __init__.py           # public engine/get_connection
  _dialect.py           # upsert, now(), introspection, sequence-reset shims
  _row.py               # mapping wrapper for sqlite3.Row compatibility
  _prebaseline.py       # one-shot SQLite fixups for existing installs
alembic.ini
alembic/
  env.py                # loads DATABASE_URL from master settings
  versions/
    000000000001_baseline.py   # creates all tables as they stand on PR merge
    000000000002_...py         # future migrations live here
```

Rules:

- *Baseline revision uses `op.create_table()` with typed Core
  columns.* No raw DDL string in the baseline. This is what makes
  the baseline portable across dialects: SQLAlchemy picks `TEXT` on
  SQLite, `TEXT` or `VARCHAR(n)` on Postgres, `VARCHAR(n)` on
  MySQL, `INTEGER` vs `BIGINT` vs `AUTOINCREMENT` identity
  correctly, and the right `CHECK` and `UNIQUE` syntax per dialect.
- *Existing SQLite installs* upgrade in-place: `alembic stamp` is
  issued once at first startup after the upgrade (when
  `alembic_version` table does not exist but `workspaces` does), to
  mark the baseline as applied. Dispositional in-code migrations
  from the pre-ADR era (envelope backfill, agents→staffs rename,
  event_actions_v2/v3, staffs_session_scope_none,
  staged_dispatches) are **preserved** in `_prebaseline.py` as
  one-shot fixups that run before Alembic takes over, only when the
  dialect is SQLite. This avoids forcing operators onto the
  snapshot-and-reload path for a simple version bump.
- *Fresh installs on any provider* get the baseline straight from
  Alembic — the pre-baseline fixups are no-ops (SQLite-only, and
  the conditional guards short-circuit on empty tables).
- *Autogenerate* is enabled but not trusted blindly: CHECK
  constraints, partial indexes, expression/functional indexes, and
  sequence-reset operations are hand-written in each revision with
  a note referencing the behaviour under each dialect.

### 5. Migration triggering and version mismatch

Resolved from OQ4: **auto-upgrade only on an empty database.
Otherwise, migrations are an explicit step.**

Startup lifespan behaviour:

1. Open a connection to `DATABASE_URL`.
2. Query the Alembic version table.
3. One of three outcomes:
   - **Empty DB** (no tables, or no `alembic_version` entry on a
     fresh DB): run `alembic upgrade head` automatically. Log it.
   - **Up-to-date DB** (`alembic_version` == code's expected head):
     proceed.
   - **Behind** (`alembic_version` < expected head, or the version
     column exists but the schema is missing columns the code
     expects): **refuse to start.** Emit a clear message:
     ```
     ERROR: DB schema is behind code (DB=abc1234, code=def5678).
     Run `scripts/db.sh migrate` before restarting master, or use
     `scripts/db.sh snapshot-and-migrate` to snapshot first.
     ```
     Exit non-zero. Do not touch the DB.
   - **Ahead** (DB has a newer `alembic_version` than this code
     knows): refuse to start with a message pointing at the release
     notes. Likely a mistaken downgrade — never silently touch a
     schema a newer version wrote.
4. Pre-baseline fixups run only on SQLite, only once (gated by
   presence of `alembic_version`), only if the DB is pre-baseline.

Operator tooling: `scripts/db.sh migrate` wraps `alembic upgrade
head`. `scripts/db.sh snapshot-and-migrate` takes a snapshot into
`${MASTER_DATA_DIR}/snapshots/` first, then migrates — the single
step most operators will use when the schema is behind.

### 6. Dialect handling

The specific SQLite-only patterns and their dialect translations:

| Pattern | SQLite | Postgres | MySQL 8.0.16+ |
|---|---|---|---|
| Upsert (ignore conflicts) | `INSERT OR IGNORE` | `INSERT ... ON CONFLICT DO NOTHING` | `INSERT IGNORE INTO` |
| Upsert with update | `ON CONFLICT (…) DO UPDATE SET x = excluded.x` | identical | `ON DUPLICATE KEY UPDATE x = VALUES(x)` |
| "Now (sub-second)" default | `unixepoch('now','subsec')` | `extract(epoch from now())` | `UNIX_TIMESTAMP(NOW(3))` |
| Partial index | `CREATE INDEX ... WHERE ...` | identical | functional index via generated column |
| Expression unique | `CREATE UNIQUE INDEX ... ON t(COALESCE(c, ''))` | identical | functional index |
| CHECK constraint | enforced | enforced | enforced (8.0.16+) |
| Introspection | `sqlite_master` / `PRAGMA table_info` | `pg_catalog` / `information_schema` | `information_schema` |
| **AUTOINCREMENT PK** | `INTEGER PRIMARY KEY AUTOINCREMENT` on `chunks.id` | `BIGSERIAL` (or `BIGINT GENERATED BY DEFAULT AS IDENTITY`) | `BIGINT AUTO_INCREMENT PRIMARY KEY` |
| **Reserved word `key`** (used in `config` and `notes`) | no quoting needed | double-quotes auto-applied by SQLAlchemy | backticks auto-applied by SQLAlchemy |
| **TEXT PK / indexed column** | `TEXT` | `TEXT` (indexable) | `VARCHAR(n)` required for PK/index |

The upsert helper is a single function `upsert(table, values,
conflict_cols, update_cols=None)` in `_dialect.py`; call sites that
today write raw `INSERT OR IGNORE` SQL change to it. For
`chunks.created_at`, the default moves off `unixepoch` into Python
code (every row that writes chunks already has a timestamp
available), closing one dialect gap without a dialect helper.

**VARCHAR widths for MySQL.** All PK and indexed `TEXT` columns in
today's schema store UUIDs — 36-character hex strings with dashes
(or 32-character hex without, depending on the generator). We pick
**`String(36)`** (`VARCHAR(36)`) uniformly. Non-PK free-form TEXT
columns (prompt templates, message bodies, result artefacts) stay
unbounded `Text`. The baseline Alembic migration uses `String(36)`
on every PK/FK/indexed UUID column so the Postgres and MySQL tables
agree.

**MySQL partial-index conversion.** The four partial indexes
(`WHERE archived_at IS NULL`, `WHERE sender_kind IS NULL`,
`WHERE dispatched_at IS NULL`, `WHERE event_type = 'topic_scheduler'`)
become functional indexes over generated columns on MySQL. The phase
1 Alembic revision enumerates each one explicitly with
`op.create_index(..., mysql_prefix='...', postgresql_where=...)`.

### 7. Configuration surface

Env vars read in `src/master/config.py`:

| Key | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | No | `postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master` | SQLAlchemy URL. Supported schemes: `postgresql+psycopg` (default), `sqlite+pysqlite`, `mysql+pymysql`. |
| `POSTGRES_PASSWORD` | Yes (when `DATABASE_URL` defaults) | — | Password for the bundled Postgres service. If unset and `DATABASE_URL` is unset, master refuses to start. |
| `DB_POOL_SIZE` | No | `5` | SQLAlchemy `pool_size`. SQLite ignores (`StaticPool`). |
| `DB_POOL_MAX_OVERFLOW` | No | `10` | SQLAlchemy `max_overflow`. SQLite ignores. |
| `DB_ECHO_SQL` | No | `false` | Dev-only flag to log every statement; off in prod. |
| `COMPOSE_PROFILES` | No | — | Docker Compose variable. See §9 for the `db-bundled` profile. |

`MASTER_DATA_DIR` keeps its current role (attachments, snapshots).
When `DATABASE_URL` is pointed at a SQLite file (explicit opt-in),
the file path lives under `MASTER_DATA_DIR` by convention. Config
reference in `docs/references/config.md` grows a row per key.

### 8. Snapshot format and tooling

Resolved from OQ3 (JSONL) and OQ9 (`scripts/db.sh` as the entry
point).

Single operator-facing entry point: `scripts/db.sh`, a bash wrapper
that internally calls `python -m src.master.db_cli`. Subcommands,
all with `--help`:

```
scripts/db.sh snapshot export \
    --source-url "$DATABASE_URL" \
    --out /path/to/snapshot_YYYYMMDDTHHMMSSZ/

scripts/db.sh snapshot import \
    --in  /path/to/snapshot_YYYYMMDDTHHMMSSZ/ \
    --target-url "postgresql+psycopg://user:pw@host:5432/db" \
    [--yes]  # skip the "target already has rows" confirmation

scripts/db.sh migrate                       # alembic upgrade head
scripts/db.sh snapshot-and-migrate          # snapshot in-place, then migrate
scripts/db.sh verify-schema                 # schema drift check
```

`scripts/db.sh --help` lists all subcommands and prints a short
description per command.

Snapshot layout (one directory per snapshot):

```
snapshot_20261024T121500Z/
  manifest.json
  workspaces.jsonl
  staffs.jsonl
  staff_sessions.jsonl
  config.jsonl
  topics.jsonl
  sessions.jsonl
  messages.jsonl
  attachments.jsonl
  chunks.jsonl
  event_actions.jsonl
  notes.jsonl
  tasks.jsonl
  staged_dispatches.jsonl
```

`manifest.json`:

```json
{
  "schema_version": "<alembic revision id>",
  "created_at": "2026-10-24T12:15:00Z",
  "source_dialect": "sqlite",
  "tables": {
    "workspaces": {"rows": 42, "sha256": "..."},
    "...": {}
  },
  "tool_version": "<app version>"
}
```

**Type normalisation on export (per m-3).** Every column type gets a
canonical JSON encoding so the file is round-trippable across
dialects:

| Column type | JSON encoding | Import coercion |
|---|---|---|
| TEXT (ISO-8601 timestamp) | string, unchanged | string, unchanged |
| TEXT (free-form) | string, unchanged | string, unchanged |
| INTEGER | number (JSON int) | int |
| REAL / Float (`chunks.created_at`, `tasks.failure_score`) | number (JSON float) | float |
| BOOLEAN (stored as 0/1: `is_default`, `silent`, `enabled`, `structured_output`) | JSON boolean (`true`/`false`) | coerced to the target dialect's bool literal (0/1 on SQLite/MySQL, `true`/`false` on Postgres) |
| JSON column (`attachments_json`, `usage_json`, `tags`, `result_artifacts`) | embedded JSON object/array (not a string) | serialized back to a JSON string before insert |
| NULL | JSON `null` | NULL |
| future BLOB | base64 string | base64-decoded to bytes |

The exporter explicitly types each column against the Alembic model
rather than relying on the source dialect's affinity, so a SQLite
export of a 0/1 integer column that is logically boolean comes out
as `true`/`false` in the JSONL. This fixes the "SQLite 0/1 round-trips
as integer on Postgres and breaks the CHECK" class of bug.

**Sequence reset on import (per B-1).** After loading rows into any
table that has an identity/sequence column (today: `chunks.id`
only; the baseline migration guarantees any future identity column
is flagged in the model), the importer runs:

- On Postgres:
  `SELECT setval(pg_get_serial_sequence('chunks', 'id'), MAX(id), true) FROM chunks;`
- On MySQL:
  `ALTER TABLE chunks AUTO_INCREMENT = <max_id + 1>;`
- On SQLite:
  `UPDATE sqlite_sequence SET seq = <max_id> WHERE name = 'chunks';`

The reset step is generalised: `_dialect.reset_sequences_for_table`
iterates every column flagged as identity in the Alembic model and
emits the right statement per dialect. The CI round-trip test asserts
that an INSERT into `chunks` after import succeeds without PK
collision.

Rules:

- One JSON object per line; values dialect-neutral per the table
  above.
- Export is streaming (`SELECT` with server-side cursor on Postgres,
  no `fetchall()` of large tables). Import is batched (`executemany`
  of 500 rows) inside a single transaction per table.
- Import *always targets an empty DB*: it runs `alembic upgrade head`
  first, then refuses to proceed if any user-data table has rows,
  unless `--yes` is passed in which case it truncates and reloads.
  See OQ10 below for the still-open "replace-only vs merge"
  decision.
- `schema_version` in the manifest must equal the current Alembic
  head on import, or the tool refuses to proceed. This fails fast on
  cross-release snapshots rather than silently losing columns.
- Row-level foreign keys are respected by load order (parents first).
  The load order is baked into the exporter.
- Round-trip test: a CI job seeds a SQLite DB, exports, imports into
  a Postgres container, resets sequences, inserts a new `chunks` row
  (asserts no PK conflict), and runs a read-side smoke test
  (count-per-table, selected row equality).

### 9. Compose and runtime changes

Resolved from OQ2 and OQ7: **dev runs on Postgres; production
supports both bundled and managed Postgres via a Compose profile.**

New file: `docker-compose.postgres.yml`. It declares the `postgres`
service behind a Docker Compose profile and the master-side env
wiring that points at it. Shape:

```yaml
services:
  postgres:
    profiles: ["db-bundled"]
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: master
      POSTGRES_USER: master
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "master", "-d", "master"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - internal

  master:
    environment:
      DATABASE_URL: ${DATABASE_URL:-postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master}
    depends_on:
      postgres:
        condition: service_healthy
        required: false   # service is optional (behind profile)

volumes:
  postgres_data:
```

The master service's `depends_on.postgres` is marked `required:
false` so compose does not error when the profile is inactive and no
`postgres` service exists.

**Profile mechanism**:

- `COMPOSE_PROFILES=db-bundled` → compose starts the bundled Postgres
  service, and the master's `DATABASE_URL` defaults to the on-network
  Postgres.
- `COMPOSE_PROFILES` unset / without `db-bundled` → the Postgres
  service does **not** start; the operator must set `DATABASE_URL`
  to an external managed Postgres (or to SQLite or MySQL).

**File layering** (preserves ADR-0016's shape — `docker-compose.yml`
and `docker-compose.deploy.yml` stay provider-neutral):

- `just dev-up` adds `-f docker-compose.postgres.yml` and sets
  `COMPOSE_PROFILES=db-bundled` by default. The persisted
  `postgres_data` volume means the test DB survives restarts, so
  seed data is stable without hand-crafting fresh fixtures per test
  run.
- `just deploy env tag` adds `-f docker-compose.postgres.yml` and
  reads `COMPOSE_PROFILES` from the environment (expected to come
  from `.env.staging` / `.env.prod`). When set to `db-bundled`, the
  singleton deploy includes the Postgres service and the
  `postgres_data` volume; when unset, the compose file layers in but
  no Postgres service runs — the operator is responsible for
  pointing `DATABASE_URL` at their managed DB.
- MySQL support ships `docker-compose.mysql.yml` with the same
  profile pattern (`db-bundled-mysql`) for operators who want to
  bundle MySQL instead of Postgres. UAT does not run against the
  bundled MySQL path.

The deploy recipe exports `COMPOSE_PROFILES` for every `docker
compose` invocation; staging and prod `.env` files declare it.

### 10. CI test matrix

Resolved from OQ5: **every supported backend's CI runs on every PR.
Postgres is top priority; SQLite and MySQL also gate merge; MySQL
does not gate UAT.**

```yaml
jobs:
  test-postgres:
    runs-on: ubuntu-latest
    services:
      postgres: { image: postgres:17-alpine, env: {...}, ports: ['5432:5432'] }
    env:
      DATABASE_URL: postgresql+psycopg://...
    # top priority — always runs first; failures gate merge

  test-sqlite:
    env:
      DATABASE_URL: sqlite+pysqlite:///:memory:
    # runs on every PR; failures gate merge

  test-mysql:
    runs-on: ubuntu-latest
    services:
      mysql: { image: mysql:8.4, env: {...}, ports: ['3306:3306'] }
    env:
      DATABASE_URL: mysql+pymysql://...
    # runs on every PR; failures gate merge; not part of UAT

  snapshot-roundtrip:
    needs: [test-postgres]
    # SQLite → export → import → Postgres smoke + sequence-reset assertion
```

Stale conditionals in the current `.github/workflows/test.yml` that
scoped Postgres to `main` or nightly are removed in Phase 0.

### 11. SQLite-upgrade safety and migration path

Switching the default to Postgres introduces a hazard: an operator
pulls the new release, restarts master, and master picks up the new
default `DATABASE_URL`. If their existing SQLite file is still on
disk, master could silently start fresh against the bundled Postgres
and the operator loses visible history.

**We do not allow that.** Startup checks (in order):

1. If `DATABASE_URL` is set explicitly, use it. The operator has
   expressed intent; no safety-net override.
2. If `DATABASE_URL` is unset and
   `${MASTER_DATA_DIR}/master_data.db` **exists** and is non-empty
   (file size > 0 and contains a `workspaces` table), **refuse to
   start**. Emit:
   ```
   ERROR: existing SQLite data detected at
   /opt/codex-slack/data/master/master_data.db, but DATABASE_URL is
   unset and the default is now Postgres. Pick one:

     (a) Keep SQLite:
         export DATABASE_URL=sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db

     (b) Migrate to Postgres (recommended):
         scripts/db.sh snapshot-and-migrate \
             --source-url "sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db" \
             --target-url "$POSTGRES_URL"
         Then set DATABASE_URL=$POSTGRES_URL.

   See docs/guides/runbooks/migrate-sqlite-to-postgres.md.
   ```
3. If `DATABASE_URL` is unset and no SQLite file is present, master
   defaults to the bundled Postgres URL (OQ2 default).

The runbook `docs/guides/runbooks/migrate-sqlite-to-postgres.md`
spells out:

- Stop master (`just undeploy staging` or local equivalent).
- Run `scripts/db.sh snapshot-and-migrate` from the host that can
  reach both the old SQLite file and the target Postgres URL.
- The script does: snapshot export → `alembic upgrade head` on target
  → import → sequence reset → verify (row counts + sample reads).
- Set `DATABASE_URL` in the environment file.
- Start master.
- Verify from the UI; keep the SQLite file around for one release
  cycle as a rollback artifact before deleting.

Rollback: set `DATABASE_URL` back to the SQLite URL. Postgres data
is thrown away; SQLite data is intact because the migration tool
never writes to the source.

### 12. Phased rollout

**Phase 0 — Groundwork (one PR, same behaviour, source changes)**

- Add SQLAlchemy + Alembic + `psycopg[binary]` + `pymysql` to
  `requirements.txt`.
- Introduce `src/master/db/` package with `engine()` and a
  backward-compatible `get_connection()` shim built on SQLAlchemy's
  dialect-aware connection.
- **Convert qmark `?` to `text()` with named `:param`** across all 16
  production and 13 test files. Automated by
  `scripts/convert_qmark_to_named.py`; human-reviewed per file.
- **Funnel direct `sqlite3.connect()` callers through the engine's
  `get_connection()` shim** in `main.py` (4 sites),
  `mqtt_client.py` (12 sites), and `notify.py` (2 sites). After
  Phase 0, no `sqlite3.connect()` remains outside `db/_prebaseline.py`
  and the snapshot CLI.
- Freeze the current schema into
  `alembic/versions/000000000001_baseline.py` using **`op.create_table()`
  with typed Core columns** (not raw DDL). The baseline matches what
  the SQLite schema looks like after all 28 `_MIGRATIONS` entries,
  all four table-rebuild helpers, and all four additive helpers have
  run.
- Add `_prebaseline.py` for in-place SQLite-only upgrades of existing
  installs.
- Add `scripts/db.sh` with `migrate`, `verify-schema`, and
  `snapshot-and-migrate` subcommands.
- Lint rule: no new `import sqlite3` outside `src/master/db/`,
  `src/master/db_cli.py`, and the test suite.

**Phase 1 — Postgres default**

- Switch `DATABASE_URL` default to the bundled Postgres URL.
- Convert all SQLite-only SQL at call sites to the dialect helpers
  (`upsert`, timestamp defaults in Python, PRAGMAs replaced by
  SQLAlchemy events for FK enforcement where supported).
- Replace `_MIGRATIONS` list and table-rebuild helpers with Alembic
  revisions. Keep `_prebaseline.py` for one release cycle (OQ11).
- Ship the snapshot CLI (`scripts/db.sh snapshot export|import`) with
  sequence-reset logic and round-trip test.
- Ship `docker-compose.postgres.yml` with the `db-bundled` profile.
- Add the SQLite-upgrade safety-net startup check (§11).
- Expand CI matrix to SQLite + Postgres + MySQL per PR. Remove stale
  conditionals that scoped non-SQLite legs to `main` or nightly.
- Update `docs/references/config.md` with `DATABASE_URL`,
  `POSTGRES_PASSWORD`, `COMPOSE_PROFILES`, and pool tunables.
- Ship `docs/guides/runbooks/migrate-sqlite-to-postgres.md`.
- Switch dev stack default to the bundled Postgres (`just dev-up`
  starts the `postgres` service via `COMPOSE_PROFILES=db-bundled`).
- Switch staging singleton to Postgres via `.env.staging` setting
  `COMPOSE_PROFILES=db-bundled`.

**Phase 2 — MySQL support hardened**

- Finalise MySQL dialect: functional-index conversions over
  generated columns for the four partial indexes, VARCHAR(36)
  widths on all UUID PK/FK/indexed columns, CHECK enforcement
  verified under MySQL 8.0.16.
- Ship `docker-compose.mysql.yml` with the `db-bundled-mysql`
  profile.
- Document the MySQL version floor and known caveats; MySQL is
  CI-only (no UAT leg).

**Phase 3 — Cleanup (optional, not blocking)**

- Delete `_prebaseline.py`.
- Delete the `get_connection()` shim once all call sites are moved
  to Core expression API (long tail).

## Alternatives Considered

- **Thin DIY dialect-abstraction layer.** Rejected in ADR-0019. Would
  keep raw SQL in-place but re-invent half of SQLAlchemy's dialect
  module and leave migrations hand-written per provider.
- **Full ORM rewrite.** Rejected in ADR-0019. Too large a diff for
  this issue; mixing async FastAPI handlers with sync workers via
  async SQLAlchemy adds a separate design problem.
- **Keep SQLite as the default and promote Postgres only as an
  overlay.** The prior revision of this doc. Rejected: dev/staging
  parity matters more than contributor friction, and the bundled
  Postgres on a persisted volume removes most of the friction
  anyway.
- **Keep SQLite as the only provider; add tooling for file-based
  replication/backup.** Does not solve the "pick a provider" ask in
  issue #268 and does not help the "SQLite isn't suitable for
  concurrent writers at scale" ADR-0005 caveat.
- **Logical replication from SQLite to Postgres as the migration
  path** (via a CDC tool). Overkill for a self-hosted single-master
  install. Snapshot + fresh import is the right simplicity tradeoff
  for our scale.
- **`pg_dump` / `mysqldump` as the snapshot format.** Simpler per
  provider but *not* portable: migrating SQLite → Postgres would
  still need a bespoke tool, so we would end up writing the
  provider-neutral one anyway. The JSONL format also doubles as a
  diff-friendly audit artefact.
- **`exec_driver_sql()` with runtime paramstyle translation.**
  Smaller Phase 0 diff — keeps `?` on SQLite call sites — but
  bypasses SQLAlchemy's type coercion (critical on Postgres for
  booleans, timestamps, and identity columns) and keeps us in the
  "re-invent half of SQLAlchemy" problem for the paramstyle layer
  specifically. Rejected.

## Resolved questions (previously open)

Decisions below are final per the project owner, 2026-10-24. They
are documented here (rather than deleted) so a future reader can see
the reasoning that led to the current shape.

- **OQ1 — Scope.** Master DB only, same scope as today's SQLite.
  Agent worker and notes MCP server are untouched.
- **OQ2 — Dev stack DB.** Postgres. The dev compose starts a
  bundled Postgres via `docker-compose.postgres.yml` with the
  `db-bundled` profile and a persisted `postgres_data` volume. Test
  env setup is a single `just dev-up`.
- **OQ3 — Snapshot format.** JSONL per table + `manifest.json`.
- **OQ4 — Migration triggering.** Auto-run `alembic upgrade head`
  **only on an empty database**. For non-empty DBs, migrations are
  an explicit `scripts/db.sh migrate` step. On version mismatch
  (DB schema behind code), master refuses to start and emits a
  clear message pointing at the migrate command and the
  snapshot-and-migrate shortcut.
- **OQ5 — CI matrix cadence.** Every PR, all three dialects.
  Postgres is top priority. Stale conditionals removed from the
  workflow file in Phase 0.
- **OQ6 — MySQL version floor.** 8.0.16+. Master refuses to start
  on older MySQL.
- **OQ7 — Deploy UX for Postgres.** Both bundled and managed, via
  a Docker Compose profile (`db-bundled`) toggled by
  `COMPOSE_PROFILES`. Bundled: profile active, bundled Postgres
  service runs, `DATABASE_URL` defaults into it. Managed: profile
  inactive, no Postgres service runs, operator sets `DATABASE_URL`
  to an external managed instance. The justfile deploy recipe
  (ADR-0016) passes `-f docker-compose.postgres.yml` on every
  invocation and exports `COMPOSE_PROFILES` from the environment —
  no new recipes, no conditional compose-file inclusion.
- **OQ8 — Secrets at rest / in flight.** Plaintext DB connections
  are acceptable. TLS is not required by this ADR.
- **OQ9 — Snapshot CLI location and name.** `scripts/db.sh`, a
  bash script with `--help`, with subcommands `snapshot export`,
  `snapshot import`, `migrate`, `snapshot-and-migrate`, and
  `verify-schema`. The script wraps Python internally
  (`python -m src.master.db_cli ...`).
- **OQ11 — Pre-baseline fixups retention.** One release cycle
  after Phase 1 lands. Then `_prebaseline.py` is deleted.
- **OQ12 — Snapshot cross-release compatibility.** Not supported.
  `schema_version` in the manifest must match the Alembic head on
  import. Cross-release upgrades go through "upgrade source first,
  then snapshot, then import."

## Open Questions

None. OQ10 resolved on design approval: snapshot import is
**replace-only**. Import refuses a non-empty target by default; with `--yes` it
truncates and reloads, so the result is an exact copy of the snapshot. Merge
is out of scope for v1.

## Implementation Plan

Milestones map 1:1 to the phases above. Each phase is a separate PR
(or small series) and ends with a release:

- *M0 (Phase 0):* Groundwork PR — SQLAlchemy/Alembic in, qmark →
  named conversion, direct `sqlite3.connect()` funnelled, baseline
  revision using `op.create_table()`, `scripts/db.sh` scaffold,
  shim in place, all tests green on SQLite. No user-visible
  change.
- *M1 (Phase 1):* Postgres default — call-site conversion to
  dialect helpers, Alembic migrations replacing `_MIGRATIONS`,
  snapshot CLI with sequence reset, Postgres compose overlay with
  the `db-bundled` profile, SQLite-upgrade safety-net, runbook, CI
  matrix expansion to all three dialects per PR. Release as the
  first version where Postgres is the default.
- *M2 (Phase 2):* MySQL hardening — dialect-specific index
  handling, VARCHAR(36) widths, MySQL compose overlay, CI leg
  stable, docs. MySQL remains CI-only (no UAT).
- *M3 (Phase 3, optional):* Cleanup — remove `_prebaseline.py` and
  the `get_connection` shim once call sites are off raw `text()`
  SQL.
