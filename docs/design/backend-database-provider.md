# Design: Backend database provider — Postgres default, SQLite and MySQL supported

**Status:** accepted
**Author:** architect
**Date:** 2026-10-24
**Related ADRs:** [ADR-0019](../decisions/0019-backend-database-provider.md); supersedes the "SQLite deferred migration" note in [ADR-0005 §4](../decisions/0005-v3-system-architecture.md); respects the single-user plaintext-secret posture of [ADR-0009](../decisions/0009-runtime-configuration-and-staff-system.md) and [ADR-0010](../decisions/0010-workspace-env-var-overrides.md); builds on the singleton deploy shape in [ADR-0016](../decisions/0016-singleton-justfile-deploys.md).

## Revision history

- **2026-10-31 (rev 5.1)** — Correction to rev 5. Two factual errors
  in the Phase 2 addendum were fixed and the surrounding snippets,
  D-rows, OQs, risk note and implementation plan brought into
  alignment.
  - *§13 gap table row 13 (booleans):* rev 5 claimed `_BOOLEAN_COLS`
    and `_fix_insert_bool_literals` "already handle call-site
    literals". The runtime SQL rewriter `_fix_insert_bool_literals`
    was **deleted** in M1 (commit `65b2a4a`; see
    `docs/knowledge-base/lessons-learned.md` 2026-10-25); it no
    longer exists in `src/`. Booleans are portable because every
    call site now binds Python `bool` through `text()` named params
    and the static guard
    `tests/master/db/test_lint.py::test_no_bare_bool_literals_in_boolean_columns`
    scans `src/` and `tests/` to enforce it. Bound Python `bool`
    values map cleanly to `TINYINT(1)` under pymysql.
  - *§13.4 D6 / §12 Phase 2 bullet / §13.8 compose / §13.9 CI
    snippet / §13.11 risk note / OQ16 / §13.12 test strategy / ADR-0020:*
    rev 5 recommended pinning `mysql:8.0` and avoiding 8.4 LTS on the
    grounds that "pymysql still relies on `mysql_native_password` for
    some configurations". Both halves are wrong. **MySQL 8.0 reached
    end-of-life in April 2026**, so pinning `mysql:8.0` means shipping
    an unsupported server. **8.4 disables `mysql_native_password` by
    default** and uses `caching_sha2_password`; **pymysql has
    supported `caching_sha2_password` since 0.9.3**. The subtlety is
    only that the first full authentication for any user over a
    non-TLS connection needs the RSA public-key exchange, which
    requires the `cryptography` Python package. The image pin is now
    **`mysql:8.4`** (LTS; `mysql:8.4` explicitly, not `mysql:8` or
    `latest`). `cryptography` becomes a hard requirement in
    `requirements.txt` (or an explicit pin if already transitive),
    enforced by a CI test that creates a fresh MySQL user and
    authenticates it over plain TCP. The 8.0.16+ floor remains for
    managed/external MySQL as the documented *minimum supported*
    server, but CI tests 8.4 only — 8.0.16–8.0.x is "best effort",
    unverified post-EOL. `--default-authentication-plugin` is **not**
    set in compose or CI (it was removed in 8.4 in favour of
    `authentication_policy`; the image default is already
    `caching_sha2_password`).
- **2026-10-31 (rev 5)** — Phase 2 (M2) detailed design added as §13.
  Enumerates every MySQL incompatibility against the Phase 1 baseline
  with file:line references, picks VARCHAR widths for every PK/UK/index
  TEXT column, maps the four partial indexes and the expression unique
  index onto MySQL 8.0.13+ generated columns, selects large-text
  column classes (TEXT / MEDIUMTEXT / LONGTEXT), decides collation
  policy (`utf8mb4_0900_as_cs` server-wide, verified at startup),
  pins the `sql_mode` requirement, and spells out the snapshot-import
  transaction strategy under MySQL's DDL auto-commit. New
  `OQ13`–`OQ17` are open for owner sign-off. §12 Phase 2 bullets and
  the Implementation Plan M2 entry are updated to match. Architecturally
  significant decisions (collation policy and baseline-change strategy)
  are also recorded in [ADR-0020](../decisions/0020-mysql-support-details.md).
- **2026-10-31 (rev 4)** — Project owner disabled the MySQL CI leg
  (`if: false` in `ci.yml`) until Phase 2 implements MySQL support. This
  narrows §10 "every supported backend on every PR" to SQLite + Postgres for
  now. Phase 2 re-enables the leg and adds it to the `Build and test` gate.
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

*Carried from M0 code review (must be addressed before or alongside the
Postgres switch):*

- **M-3 (carried):** `scope_id IS :scope_id` in `staffs.py` is invalid on
  Postgres (`IS` requires a literal, not a parameter; replace with
  `scope_id = :scope_id OR (scope_id IS NULL AND :scope_id IS NULL)` or
  use a dialect helper).
- **m-2 (carried):** `schema_info()` in `src/master/db/__init__.py` uses
  `PRAGMA table_info`, which is SQLite-only. Replace with
  `sqlalchemy.inspect()` so it works on all dialects.
- **m-3 (carried):** Driver packages (`psycopg[binary]`, `pymysql`) are
  installed unconditionally. This is intentional (the design wants them
  available); keep as-is but note in the dependency rationale.
- **m-5 (carried):** Mixed commit ownership in `_prebaseline.py` helpers —
  some call `conn.commit()` inside the helper, others leave commit to the
  caller. Standardise to caller-commits before the first Alembic revision
  that touches any of these tables.

*Phase 1 primary work:*

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

**Phase 2 — MySQL support hardened** (full detail in §13; architecturally
significant decisions in [ADR-0020](../decisions/0020-mysql-support-details.md))

- New Alembic revision `000000000002_mysql_dialect_fixes.py` (the baseline
  stays frozen — see ADR-0020 strategy S2):
  - Widen every PK/UK/index TEXT column to a `String(n)` per the §13 widths
    table (UUID keys 36, short-enum columns 16–32, free-form names 128,
    refs 255).
  - Drop the `server_default="master"` from `workspaces.repo_ref` (TEXT
    columns can't carry defaults on MySQL); backfill in Python at insert.
  - Replace the four partial indexes
    (`idx_workspaces_active_name`, `idx_messages_envelope_backfill`,
    `idx_event_actions_scheduler`, `idx_staged_dispatches_undispatched`)
    with a STORED generated boolean column + a full index on
    `(generated_col, …)` under MySQL; keep the native partial-index
    form under SQLite/Postgres via `sqlite_where` / `postgresql_where`.
  - Replace `idx_staffs_scope_name`'s `COALESCE(scope_id,'')` expression
    with a NOT NULL `scope_id_key String(36)` STORED generated column
    (`COALESCE(scope_id,'')`) and a plain unique index on
    `(scope_type, scope_id_key, name)`.
  - Reclassify large-text columns to `MEDIUMTEXT`/`LONGTEXT` via
    `Text().with_variant(…)` so the Postgres/SQLite DDL is unchanged
    (both treat the variants as plain TEXT) but MySQL gets a bigger
    body — see §13 for per-column choices.
  - Promote `chunks.created_at` from `sa.Float()` to
    `sa.Float(precision=53)` so MySQL emits DOUBLE instead of
    4-byte FLOAT.
- `src/master/db/_dialect.py`:
  - Add a `verify_mysql_server_setup(conn)` function called from
    `startup_db` on the MySQL dialect; refuses to start if the server
    is MySQL < 8.0.16, if `@@collation_server`/`@@collation_database`
    is not `utf8mb4_0900_as_cs`, or if the required `sql_mode` flags
    (`STRICT_TRANS_TABLES`, `ONLY_FULL_GROUP_BY`, `NO_ZERO_IN_DATE`,
    `NO_ZERO_DATE`, `ERROR_FOR_DIVISION_BY_ZERO`) are missing.
  - Extend `DBIntegrityError` in `src/master/db/__init__.py` to include
    `pymysql.err.IntegrityError`.
- `requirements.txt`: add **`cryptography`** (or verify it is already a
  transitive dependency and pin it directly) — required for pymysql's
  RSA public-key exchange on the first `caching_sha2_password`
  authentication over a non-TLS connection, which is the default on
  8.4 since `mysql_native_password` is disabled. A new CI job
  authenticates a *fresh* MySQL user over plain TCP to prove the
  path and to guard against the dependency disappearing silently.
- `src/master/db/snapshot.py` and `tests/conftest.py` move the MySQL
  reset path from `TRUNCATE` (DDL, auto-commits) to `DELETE` inside
  the single `engine.begin()` transaction under
  `SET FOREIGN_KEY_CHECKS=0`; sequence reset
  (`ALTER TABLE … AUTO_INCREMENT = n`) stays post-commit (DDL, cannot
  be inside the transaction).
- Ship `docker-compose.mysql.yml` with the `db-bundled-mysql` profile.
  Image pinned to **`mysql:8.4`** (LTS) — explicitly `mysql:8.4`, not
  `mysql:8` or `latest`, so a tag change cannot silently swap majors.
  MySQL 8.0 reached end-of-life in April 2026; the 8.0.16+ floor is
  preserved for *managed/external* MySQL as the documented minimum,
  but CI exercises 8.4 only — see §13 D6 for the pymysql /
  `caching_sha2_password` / `cryptography` requirements. Server flags
  `--character-set-server=utf8mb4`,
  `--collation-server=utf8mb4_0900_as_cs`,
  `--sql-mode=STRICT_TRANS_TABLES,ONLY_FULL_GROUP_BY,…`. Do **not**
  set `--default-authentication-plugin` (removed in 8.4; replaced by
  `authentication_policy`). Keep the CI compose service in
  `docker-compose.ci.yml` in sync with the same flags. MySQL always
  requires an explicit `DATABASE_URL`; `resolve_db_url()` does *not*
  grow a bundled-MySQL default.
- CI: re-enable `test-mysql` (`if: false` removed), remove
  `continue-on-error`, add it to the `Build and test` gate's `needs`.
  Add a new `baseline-ddl-stability` job that diffs the compiled
  baseline DDL for SQLite + Postgres against a committed fixture to
  prove the Phase 2 revision does not disturb the baseline. Add a
  SQLite → MySQL snapshot round-trip CI job.
- Document the MySQL version floor, collation requirement, and
  managed-instance setup checklist in
  `docs/references/config.md` and a new runbook
  `docs/guides/runbooks/mysql-setup.md`. MySQL is CI-only (no UAT
  leg).

**Phase 3 — Cleanup (optional, not blocking)**

- Delete `_prebaseline.py`.
- Delete the `get_connection()` shim once all call sites are moved
  to Core expression API (long tail).

### 13. Phase 2 (M2) detailed design — MySQL support

Phase 1 shipped SQLAlchemy Core + Alembic with Postgres as the default
and SQLite as the alternative. The baseline revision
(`000000000001_baseline.py`) uses typed Core columns and already
carries `sqlite_where` / `postgresql_where` placeholders in a handful
of partial indexes, but it was never exercised end-to-end on MySQL —
the first CI run against `mysql:8.0` fails on `CREATE TABLE workspaces`
with `(1101, "BLOB, TEXT, GEOMETRY or JSON column 'repo_ref' can't
have a default value")`, which blocks every subsequent test. Rev 4 of
this doc disabled the leg; Phase 2 (M2) brings it back.

The architectural decisions behind this section (collation policy and
baseline-change strategy) are recorded in
[ADR-0020](../decisions/0020-mysql-support-details.md); the mechanics
and the gap list live here.

#### 13.1 Gap list (baseline vs MySQL 8.0.16)

Every incompatibility against the Phase 1 schema, with file and
line references. Severity is **blocker** (schema cannot be
created / tests can't run), **behaviour** (schema is created but
query semantics diverge), or **operational** (not a code bug but a
startup / verification gap).

| # | Gap | Site | Severity | Fix |
|---|-----|------|----------|-----|
| 1 | `TEXT NOT NULL DEFAULT 'master'` on `workspaces.repo_ref` | `alembic/versions/000000000001_baseline.py:38` | **blocker** — error 1101 observed in CI | Drop the `server_default`; set the value in Python at every insert site (same approach the design took for `chunks.created_at`). Narrower `String(255)` makes this a true VARCHAR with a default, but we prefer the no-default form because `repo_ref` is always written explicitly today. |
| 2 | `TEXT NOT NULL DEFAULT "'[]'"` on `notes.tags` | `baseline.py:260` | **blocker** — same 1101 class | Drop `server_default`; write `"[]"` from Python at insert (one call site, `src/master/notes.py`). |
| 3 | PK on three TEXT columns `(scope_type, scope_id, staff_name)` | `baseline.py:95-101` (`staff_sessions`) | **blocker** — MySQL rejects TEXT in PK | `String(32)` / `String(36)` / `String(128)`. |
| 4 | PK on three TEXT columns `(scope_type, scope_id, key)` plus reserved word `key` | `baseline.py:109-125` (`config`) | **blocker** — TEXT in PK; `key` reserved | `String(32)` / `String(36)` / `String(128)`. SQLAlchemy already quotes `key` as `` `key` `` on MySQL. |
| 5 | UNIQUE constraint on three TEXT columns `(scope_type, scope_id, key)` | `baseline.py:271` (`notes`) | **blocker** — TEXT in UK | Same widths as #4. |
| 6 | Expression UNIQUE index `COALESCE(scope_id,'')` + three TEXT cols | `baseline.py:76-83` (`idx_staffs_scope_name`) | **blocker** — TEXT in UK, and functional index needs 8.0.13+ generated column | Add `scope_id_key String(36) NOT NULL GENERATED ALWAYS AS (COALESCE(scope_id,'')) STORED`; unique index becomes `(scope_type, scope_id_key, name)` with `String` widths. |
| 7 | Partial index `WHERE archived_at IS NULL` | `baseline.py:42-49` (`idx_workspaces_active_name`) | **blocker** — MySQL has no partial indexes | Generated `is_active TINYINT(1) NOT NULL GENERATED ALWAYS AS (archived_at IS NULL) STORED`; unique index `(is_active, name)` under MySQL only, keep `postgresql_where` / `sqlite_where` elsewhere. |
| 8 | Partial index `WHERE sender_kind IS NULL` | `baseline.py:177-183` (`idx_messages_envelope_backfill`) | **blocker** — same | Same pattern: `needs_envelope_backfill` generated TINYINT. |
| 9 | Partial index `WHERE event_type = 'topic_scheduler'` | `baseline.py:244-250` (`idx_event_actions_scheduler`) | **blocker** — same | Same pattern: `is_scheduler` generated TINYINT. |
| 10 | Partial index `WHERE dispatched_at IS NULL` | `baseline.py:303-309` (`idx_staged_dispatches_undispatched`) | **blocker** — same | Same pattern: `is_undispatched` generated TINYINT. |
| 11 | TEXT in non-PK indexes | multiple: `idx_messages_task` (`messages.task_id` Text), `idx_messages_reply_to` (`messages.reply_to_message_id` Text), `idx_sessions_topic_agent` (`sessions.agent_name` Text), `idx_chunks_message` (`chunks.message_id` Text), `idx_chunks_topic` (`chunks.topic_id` Text), `idx_event_actions_scope_event` (four TEXT cols), `idx_notes_scope` (two TEXT cols), `idx_tasks_root` (`tasks.root_task_id` Text), `idx_tasks_topic_state` (`tasks.state` Text), `idx_tasks_parent` (`tasks.parent_task_id` Text), `idx_staged_dispatches_prompt` (`prompt_message_id` Text) | **blocker** — MySQL rejects TEXT in index | `String(n)` per the widths table in §13.2. |
| 12 | CHECK constraints on `scope_type`, `session_scope`, `event_type`, `direction`, `kind`, `state`, `dispatcher_kind`, `last_run_status`, `timing`, and the composite `ck_event_actions_schedule` | throughout baseline (every CheckConstraint call) | **blocker on <8.0.16, behaviour on 8.0.16+** — CHECK enforcement lands in 8.0.16 | No code change; startup guard (§13.4) refuses to run on <8.0.16. |
| 13 | Boolean columns: `staffs.is_default`, `messages.silent`, `event_actions.enabled`/`structured_output`/`silent` | baseline Boolean() columns | **behaviour** — SA maps to `TINYINT(1)` by default; `sa.true()`/`sa.false()` compile to `1`/`0` under MySQL | No code change. Note: the runtime SQL rewriter `_fix_insert_bool_literals` was **deleted** in M1 (commit `65b2a4a`, see `docs/knowledge-base/lessons-learned.md` 2026-10-25) — it does not exist in `src/` anymore. Booleans are portable because every call site now binds Python `bool` through `text()` named params, and `tests/master/db/test_lint.py::test_no_bare_bool_literals_in_boolean_columns` scans both `src/` and `tests/` to enforce it (static lint, no runtime path). Bound Python `bool` values map cleanly to `TINYINT(1)` under pymysql, so baseline `Boolean()` columns need no MySQL-specific coercion. Add MySQL-leg coverage of the existing boolean round-trip tests. |
| 14 | AUTOINCREMENT PK (`chunks.id`) sequence reset | `snapshot.py:_reset_sequence` | **operational** — already implemented via `ALTER TABLE … AUTO_INCREMENT = n` | None; add a round-trip test on MySQL. |
| 15 | `INSERT OR IGNORE` / `ON CONFLICT DO UPDATE` → `INSERT IGNORE` / `ON DUPLICATE KEY UPDATE` | `src/master/db/_dialect.py:upsert` | **operational** — already dispatches on dialect | None; add MySQL coverage to upsert unit tests. |
| 16 | `json_each(col)` → MySQL `JSON_CONTAINS(col, JSON_QUOTE(:param))` | `src/master/db/_dialect.py:json_array_contains_fragment` | **operational** — already dispatches | None; `src/master/notes.py` call site is already dialect-aware. |
| 17 | `DBIntegrityError` tuple does not include `pymysql.err.IntegrityError` | `src/master/db/__init__.py:33-37` | **blocker** — MySQL unique-violation exceptions escape as uncaught `IntegrityError` from SQLAlchemy wrapping | Add a conditional `try: from pymysql.err import IntegrityError …` and extend the tuple. |
| 18 | DDL auto-commit vs single-transaction snapshot import | `src/master/db/snapshot.py:_acquire_truncate_locks_in_txn` and the `engine.begin()` block; `tests/conftest.py:_non_sqlite_truncate` MySQL branch | **blocker** for snapshot atomicity — MySQL commits implicitly on `TRUNCATE`, `ALTER TABLE`, `CREATE INDEX`, so the "lock + truncate + load" transaction is torn in half on MySQL | (a) Replace `TRUNCATE TABLE` with `DELETE FROM` inside the transaction, protected by `SET FOREIGN_KEY_CHECKS=0` for the duration of the transaction. (b) Keep `ALTER TABLE … AUTO_INCREMENT = n` outside the transaction (it's DDL and already is, see `_reset_sequence`). (c) `SET SESSION innodb_lock_wait_timeout = <seconds>` (row-lock timeout) **and** `SET SESSION lock_wait_timeout = <seconds>` (metadata lock timeout) at transaction start so a running master process either releases its locks quickly or we fail fast. The test conftest adopts the same `DELETE` approach so test isolation stays atomic. |
| 19 | `ONLY_FULL_GROUP_BY` under MySQL `sql_mode` | `src/master/topics.py:list_recent_topics`, `src/master/main.py:_close_stale_streams` | **behaviour** — both already fixed in M1 (see lessons-learned 2026-10-31); verify under MySQL CI | None in code; CI gates it. New grep-based lint flagging bare GROUP BY on joined queries. |
| 20 | `\|\|` string concat | n/a — confirmed by grep: no `\|\|` in `src/master/*.py` SQL | **n/a** | None. If one is introduced later, require `CONCAT()` or set `PIPES_AS_CONCAT` in `sql_mode`. |
| 21 | `LIMIT` in subqueries | n/a — confirmed by grep: every `LIMIT` is top-level in its statement | **n/a** | None. 8.0.16+ accepts `LIMIT` inside subqueries anyway. |
| 22 | utf8mb4 and 4-byte emoji | server flag `character-set-server=utf8mb4` already set in CI compose | **operational** | Verified by startup guard; URL carries `?charset=utf8mb4`. |
| 23 | TEXT 64 KB size limit for message bodies | `messages.text` (Text), `messages.transcript` (Text), `tasks.result_artifacts` (Text), `event_actions.prompt_template` (Text), `event_actions.last_run_output` (Text), `tasks.result_summary` (Text), `tasks.acceptance_criteria` (Text), `tasks.goal` (Text), `topics.worktree_path` (Text), `staffs.system_prompt` (Text), `staffs.extra_flags` (Text), `topics.veto_reason` (Text), `messages.usage_json` (Text), `messages.attachments_json` (Text), `config.value` (Text), `notes.value` (Text) | **behaviour** (hits silently as `Data too long` on insert of >64 KB) | Promote selected columns to `MEDIUMTEXT` (16 MB) or `LONGTEXT` (4 GB) via `Text().with_variant(mysql.LONGTEXT, 'mysql')`. Choices in §13.3. |
| 24 | Float precision for `chunks.created_at` | `baseline.py:198` (`sa.Float()`) | **behaviour** — MySQL `FLOAT` is 4-byte, ~7 significant digits; Unix epoch with sub-second resolution needs ~14 | Change to `sa.Float(precision=53)` → MySQL `DOUBLE`, Postgres `DOUBLE PRECISION`, SQLite `REAL` (unchanged). |
| 25 | Default collation `utf8mb4_0900_ai_ci` is case- and accent-insensitive | server-wide, affects every `=`/UNIQUE/`ORDER BY` on text columns | **behaviour** — differs from SQLite BINARY and Postgres default | Server-wide `utf8mb4_0900_as_cs`, verified at startup. See ADR-0020 (C2). |
| 26 | Sorting under non-default collation | `ORDER BY` on `workspaces.name`, `staffs.name`, etc. | **behaviour** — follows #25 | Same fix as #25. |
| 27 | `alembic_version` column width | Alembic default `VARCHAR(32)`; our revision ids are 12 chars | **n/a** | None. |
| 28 | Driver: pymysql vs mysqlclient | `requirements.txt` already uses `pymysql` | **operational** | Keep `pymysql`: pure Python, no C build dep in the test image. The SQLAlchemy URL stays `mysql+pymysql://`. |
| 29 | Startup version check (8.0.16 floor) | no code today | **operational** | `startup_db` calls `_dialect.verify_mysql_server_setup(conn)` which runs `SELECT VERSION()`, `SELECT @@collation_server`, `SELECT @@collation_database`, `SELECT @@sql_mode`, and prints a sharp refusal with the compose-flag fix if any check fails. |

#### 13.2 VARCHAR widths for every PK/UK/index TEXT column

The current baseline sets `String(36)` on the UUID identity columns
and leaves everything else `Text()`. Phase 2 widens every column that
participates in a PK/UK/index. Non-PK/UK free-form columns stay
`Text()` — see §13.3 for the large-text classification.

| Column(s) | New width | Reason |
|---|---|---|
| `workspaces.id`, `topics.id`, `sessions.id`, `messages.id`, `attachments.id`, `event_actions.id`, `notes.id`, `tasks.id`, `staged_dispatches.id`, `staffs.id` (already) | `String(36)` | UUID with dashes. |
| `staff_sessions.scope_type`, `config.scope_type`, `notes.scope_type`, `staffs.scope_type`, `event_actions.scope_type`, `tasks.dispatcher_kind`, `attachments.direction`, `event_actions.timing`, `staged_dispatches.kind`, `tasks.state`, `event_actions.event_type`, `staffs.session_scope`, `event_actions.last_run_status`, `topics.veto_status` | `String(32)` | Short enum values; keeps room for future values. |
| `staff_sessions.scope_id`, `config.scope_id`, `notes.scope_id`, `staffs.scope_id`, `event_actions.scope_id` | `String(36)` | UUID or empty string sentinel. |
| `staff_sessions.staff_name`, `config.key`, `notes.key`, `staffs.name`, `topics.current_staff_name`, `event_actions.staff_name`, `tasks.assignee_name`, `tasks.dispatcher_name`, `sessions.agent_name`, `messages.agent_name`, `messages.sender_name`, `messages.receiver_name` | `String(128)` | Operator-facing name; practical cap. |
| `messages.sender`, `messages.sender_kind`, `messages.receiver_kind` | `String(32)` | `user`/`agent`/etc. |
| `topics.branch_name` | `String(255)` | Git refname bound. |
| `topics.base_sha` | `String(64)` | SHA-256 upper bound. |
| `workspaces.repo_ref` | `String(255)` | Branch/ref string, now with no server default. |
| `messages.task_id`, `messages.reply_to_message_id`, `messages.event_action_id`, `tasks.root_task_id`, `tasks.parent_task_id`, `staged_dispatches.prompt_message_id`, `staged_dispatches.topic_id`, `chunks.message_id`, `chunks.topic_id` | `String(36)` | UUID keys. (Note: these are not FK-declared in the baseline, but they are indexed.) |
| `messages.dispatch_token` | `String(64)` | Opaque token. |
| `staffs.adapter`, `staffs.model` | `String(64)` / `String(128)` | Short identifier / model slug. |
| `sessions.adapter` | `String(64)` | Short identifier. |
| `attachments.filename` | `String(255)` | Posix filename cap. |
| `attachments.mime_type` | `String(128)` | RFC 6838 bound. |
| `event_actions.cron_expr` | `String(255)` | 5–7 fields; comfortable cap. |
| `workspaces.container_name` | `String(255)` | Docker container name cap. |
| `workspaces.last_agent_state` | `String(64)` | Short state tag. |

ISO-8601 timestamp columns (`created_at`, `updated_at`, `archived_at`,
`last_*_at`, `started_at`, `dispatched_at`, `closed_at`) are 20
characters (`YYYY-MM-DDTHH:MM:SSZ`); they are not indexed in the
baseline except via composite indexes that already name a String
column first, so they can stay as `Text()` with a `String(32)`
promotion where (and only where) they appear in a MySQL index on
their own. In the current baseline none do.

#### 13.3 Large-text column classification

MySQL's TEXT type is 64 KB. For the long-form columns the master
service writes (full agent transcripts, dispatch bodies, JSON blobs
for usage/attachments), 64 KB is tight. Phase 2 uses
`Text().with_variant(mysql.MEDIUMTEXT, 'mysql')` (16 MB) or
`.with_variant(mysql.LONGTEXT, 'mysql')` (4 GB) so SQLite/Postgres
DDL is unchanged.

| Column | MySQL type | Rationale |
|---|---|---|
| `messages.transcript` | `LONGTEXT` | Full conversation serialization; can exceed 16 MB on long topics. |
| `messages.text` | `MEDIUMTEXT` | Message body; large but bounded in practice. |
| `messages.usage_json`, `messages.attachments_json` | `MEDIUMTEXT` | Tool-call usage records can be large. |
| `tasks.result_artifacts` | `LONGTEXT` | May embed structured task output (code, logs). |
| `tasks.result_summary`, `tasks.goal`, `tasks.acceptance_criteria` | `MEDIUMTEXT` | Free text, could be long but 16 MB is ample. |
| `event_actions.prompt_template`, `event_actions.last_run_output` | `MEDIUMTEXT` | Rendered prompts and run output. |
| `staffs.system_prompt` | `MEDIUMTEXT` | Prompt bodies. |
| `staffs.extra_flags`, `staffs.agent` | `TEXT` (stays 64 KB) | Short strings or small JSON. |
| `config.value`, `notes.value` | `MEDIUMTEXT` | Note bodies / arbitrary config values. |
| `notes.tags` | `TEXT` | Small JSON array in practice (default `[]`). |
| `topics.veto_reason`, `topics.worktree_path` | `TEXT` | Short strings. |
| `messages.interrupt_reason` | `TEXT` | Short string. |

#### 13.4 Behaviour decisions and owner sign-offs

The table below enumerates every Phase 2 decision that changes
observable behaviour or operator surface. Items marked **sign-off
needed** must be signed off by the project owner before the Phase 2
PR opens. Items marked **engineering** are mechanical.

| # | Decision | Recommendation | Sign-off |
|---|----------|----------------|----------|
| D1 | Collation | Server-wide `utf8mb4_0900_as_cs`, verified at startup; `startup_db` refuses to start on a MySQL instance whose `@@collation_server` or `@@collation_database` is not that value. Rationale and alternatives: ADR-0020 §C1–C4. | **needed** |
| D2 | Large-text column classes | `TEXT` / `MEDIUMTEXT` / `LONGTEXT` per the §13.3 table, via `Text().with_variant(mysql.…, 'mysql')`. SQLite/Postgres DDL unchanged. | **needed** (small chance owner wants LONGTEXT everywhere for simplicity) |
| D3 | `sql_mode` verification at startup | `startup_db` runs `SELECT @@sql_mode` under the MySQL dialect; refuses to start unless the set includes `STRICT_TRANS_TABLES`, `ONLY_FULL_GROUP_BY`, `NO_ZERO_IN_DATE`, `NO_ZERO_DATE`, `ERROR_FOR_DIVISION_BY_ZERO`. `PIPES_AS_CONCAT` not required (we don't use `\|\|`); documented so a future caller can opt in. | **needed** |
| D4 | Snapshot import transaction strategy | Replace `TRUNCATE` with `DELETE FROM` inside the single `engine.begin()` transaction under session-scoped `SET FOREIGN_KEY_CHECKS=0`. Row-lock timeout via `SET SESSION innodb_lock_wait_timeout`; metadata-lock timeout via `SET SESSION lock_wait_timeout`. Sequence reset (`ALTER TABLE … AUTO_INCREMENT = n`) stays outside the transaction — it is DDL and would auto-commit anyway. Same change applied to the `_non_sqlite_truncate` fixture in `tests/conftest.py`. | engineering (no behaviour change for operators) |
| D5 | Baseline revision strategy | Baseline (`000000000001`) is frozen; all Phase 2 schema changes land in a new revision `000000000002_mysql_dialect_fixes.py`. Any MySQL-only kwargs that don't change SQLite/PG DDL also go into the new revision. See ADR-0020 strategy S2. Verification: a new `baseline-ddl-stability` CI job compiles the baseline against SQLite and PostgreSQL dialects and diffs the normalised DDL against a committed fixture (`tests/master/db/fixtures/baseline_ddl_{sqlite,postgresql}.sql`). | **needed** |
| D6 | `docker-compose.mysql.yml` and image pin | Ship with `db-bundled-mysql` profile mirroring the Postgres overlay. Image pinned to **`mysql:8.4`** (LTS) — explicitly `mysql:8.4`, **not** `mysql:8` or `latest`, so a tag change does not quietly swap us to another major. MySQL **8.0 reached end-of-life in April 2026**; shipping on `mysql:8.0` would ship an unsupported server. 8.4 **disables `mysql_native_password` by default** and uses `caching_sha2_password`. PyMySQL has supported `caching_sha2_password` since 0.9.3; the subtlety is that the *first* full authentication for any user over a non-TLS connection needs the RSA public-key exchange, which requires the `cryptography` package. Fast-auth after the server caches the credentials does not. **Requirements:** (a) add `cryptography` to `requirements.txt`, or verify it is already a transitive dependency of our existing stack and pin it directly to prevent a silent drop; (b) a CI test that creates a fresh user on the 8.4 service and authenticates it over a plain TCP (non-TLS) connection, so the RSA key-exchange path is exercised and we don't regress by losing `cryptography`. The **8.0.16 floor remains for managed/external MySQL** — that is the documented minimum supported server — but CI tests 8.4 **only**, so 8.0.16–8.0.x is "best effort" and unverified post-EOL. Server flags: `--character-set-server=utf8mb4`, `--collation-server=utf8mb4_0900_as_cs`, `--sql-mode=STRICT_TRANS_TABLES,ONLY_FULL_GROUP_BY,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO`. Do **not** set `--default-authentication-plugin` — the flag was **removed in 8.4** in favour of `authentication_policy`; the image default (`caching_sha2_password`) is already what we want, so no auth-plugin flag is set in compose or CI. Volume `mysql_data:/var/lib/mysql`. Healthcheck `mysqladmin ping` (same as CI). Coexists with the Postgres overlay via Compose's `required: false` depends_on. CI compose service in `docker-compose.ci.yml` is updated to the same flags. | **needed** (image-pin choice) |
| D7 | Bundled-MySQL default URL | `resolve_db_url()` does **not** gain a bundled-MySQL default. MySQL always requires an explicit `DATABASE_URL` of the form `mysql+pymysql://user:pw@host:3306/master?charset=utf8mb4`. Keeps the Postgres default unambiguous and keeps the SQLite-safety-net check (§11) a two-way choice. | engineering |
| D8 | CI gating | Re-enable `test-mysql` (remove `if: false`, remove `continue-on-error`), add it to the `Build and test` gate's `needs: [test, test-mysql]`. New `baseline-ddl-stability` job runs on every PR. New `snapshot-roundtrip-mysql` CI job seeds a SQLite DB, exports, imports into a MySQL container, resets sequences, inserts a new `chunks` row, and runs a read-side smoke test. | engineering |
| D9 | Startup version check | `startup_db` runs `SELECT VERSION()` on the MySQL dialect; parses `<major>.<minor>.<patch>` from the first segment (strip `-mysql`/`-ndb` suffixes); refuses to start on `(major, minor, patch) < (8, 0, 16)` with a clear message. The same function performs D1 and D3 checks (collation + sql_mode) in the same pass. | engineering |

#### 13.5 Alembic revision shape

`alembic/versions/000000000002_mysql_dialect_fixes.py` is a single
revision that is safe to apply on all three dialects.

- Column type changes are expressed via `op.alter_column(... type_=...)`
  and are no-ops in practice on SQLite/Postgres (SQLite's affinity
  system treats VARCHAR(n) and TEXT identically; Postgres treats
  VARCHAR(n) as TEXT + CHECK(length <= n), which does take a brief
  metadata lock but no table rewrite).
- `op.alter_column("workspaces", "repo_ref", server_default=None)`
  drops the server-side default; the application already supplies the
  value on insert.
- Generated columns use `sa.Computed("…", persisted=True)` on
  SQLAlchemy, scoped to `mysql_` kwargs. Under SQLite/Postgres the
  generated column is omitted and the partial / expression index
  shape is retained via `sqlite_where` / `postgresql_where`. Under
  MySQL the generated column is added and the index uses it.
- Index replacement is sequenced: `op.drop_index(name, table_name=…)`
  → `op.add_column(...)` (MySQL only) → `op.create_index(...)` with
  dialect-specific column list.
- Large-text promotions use
  `op.alter_column(..., type_=sa.Text().with_variant(mysql.MEDIUMTEXT,
  'mysql'))`.

Downgrade() reverses each step. The revision passes `alembic
upgrade head` → `alembic downgrade -1` round-tripping as part of CI.

#### 13.6 Snapshot and test-isolation changes

Both `src/master/db/snapshot.py` and `tests/conftest.py` currently use
`TRUNCATE` under the MySQL branch, which is DDL on MySQL and auto-commits:

- *snapshot.py.* The `engine.begin()` block that acquires locks and
  reloads data assumes a single atomic transaction. Under MySQL the
  TRUNCATE inside it would commit the "clean the table" phase before
  the load started, exposing a half-empty database to any concurrent
  reader. Replace with `DELETE FROM "…"` inside the transaction,
  wrapped in `SET FOREIGN_KEY_CHECKS=0` / `=1` so parent rows can be
  deleted first per `TRUNCATE_ORDER`. `_acquire_truncate_locks_in_txn`
  grows a MySQL branch that sets both
  `innodb_lock_wait_timeout` (row locks) and `lock_wait_timeout`
  (metadata locks).
- *conftest.py.* `_non_sqlite_truncate` MySQL branch moves to the
  same pattern. Test isolation stays per-test and keeps the fast-path
  no-commit behaviour.

Sequence reset (`ALTER TABLE … AUTO_INCREMENT = n`) stays outside the
load transaction — it is DDL and cannot be bundled with
`engine.begin()` on MySQL. The current `_reset_sequence` already
emits it in a separate `engine.begin()` block after the main one, so
no code change is needed there.

#### 13.7 Startup verification

`src/master/db/_dialect.py` adds:

```python
def verify_mysql_server_setup(conn) -> None:
    """Refuse to start on a non-conformant MySQL server.

    Checks server version (>= 8.0.16), collation (server + database
    both utf8mb4_0900_as_cs), and sql_mode (contains the required
    strict flags).  Raises SystemExit(1) on failure with the fix.
    """
```

Invoked from `startup_db` under `dialect == 'mysql'` after connection
retry succeeds and before the Alembic version check. The function is
pure read: no DDL.

A matching unit test exercises each refusal path with a mocked
connection returning deliberately-wrong `@@collation_server` /
`@@sql_mode` / `VERSION()`.

#### 13.8 Compose overlay

`docker-compose.mysql.yml`:

```yaml
services:
  mysql:
    profiles: ["db-bundled-mysql"]
    image: mysql:8.4        # 8.4 LTS; 8.0 is EOL (April 2026). Floor for managed MySQL is 8.0.16, enforced at startup.
    command:
      - "--character-set-server=utf8mb4"
      - "--collation-server=utf8mb4_0900_as_cs"
      - "--sql-mode=STRICT_TRANS_TABLES,ONLY_FULL_GROUP_BY,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO"
    environment:
      MYSQL_DATABASE: master
      MYSQL_USER: master
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:-}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:-${MYSQL_PASSWORD:-}}
    volumes:
      - mysql_data:/var/lib/mysql
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -uroot -p\"$$MYSQL_ROOT_PASSWORD\" --silent"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 30s
    networks:
      - internal
    restart: unless-stopped

  master:
    environment:
      DATABASE_URL: ${DATABASE_URL:-}   # same posture as Postgres: do not default here
    depends_on:
      mysql:
        condition: service_healthy
        required: false

volumes:
  mysql_data:
```

Operators who want bundled MySQL set `COMPOSE_PROFILES=db-bundled-mysql`
and `DATABASE_URL=mysql+pymysql://master:${MYSQL_PASSWORD}@mysql:3306/master?charset=utf8mb4`.
The empty `${DATABASE_URL:-}` keeps the SQLite-safety-net check live
(same reason as the Postgres overlay — see the 2026-10-25 lesson).
UAT does not run against this overlay.

#### 13.9 CI changes

- Remove `if: false` and `continue-on-error` from `test-mysql`. The
  job already builds + runs `.sre/test-db.sh mysql`.
- Add `test-mysql` to `test-gate.needs` so the stable `Build and test`
  required check now requires all three legs.
- Align `docker-compose.ci.yml`'s `mysql` service `command` to the
  same flags as the overlay (`collation-server=utf8mb4_0900_as_cs`,
  the strict `sql_mode` set) — the current CI compose uses
  `utf8mb4_0900_ai_ci` and no `sql_mode` flag.
- New job `baseline-ddl-stability`:

```yaml
baseline-ddl-stability:
  name: Baseline DDL stability
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: { python-version: "3.11" }
    - run: pip install -r requirements.txt
    - name: Compile baseline for sqlite + postgres
      run: python scripts/dump_baseline_ddl.py \
             --dialect sqlite     --out /tmp/baseline_sqlite.sql && \
           python scripts/dump_baseline_ddl.py \
             --dialect postgresql --out /tmp/baseline_postgres.sql
    - name: Diff against committed fixtures
      run: diff -u tests/master/db/fixtures/baseline_ddl_sqlite.sql     /tmp/baseline_sqlite.sql && \
           diff -u tests/master/db/fixtures/baseline_ddl_postgresql.sql /tmp/baseline_postgres.sql
```

The `scripts/dump_baseline_ddl.py` helper (part of the Phase 2 PR)
runs the `upgrade()` of the baseline revision against a mock
engine and emits the compiled DDL as text. The fixture files are
committed in the same PR that produces them, so the diff is zero on
day one and any later schema change forces an intentional fixture
update (which also must get reviewed).

- New job `snapshot-roundtrip-mysql`:

```yaml
snapshot-roundtrip-mysql:
  needs: [test-mysql]
  runs-on: ubuntu-latest
  steps:
    # Seed a SQLite DB via tests/fixtures, export, spin up mysql:8.4,
    # import, run `_reset_sequence`, insert a chunks row, read it back,
    # assert row counts per table.
```

#### 13.10 Call-site review

Beyond the schema fixes, Phase 2 must verify the handful of raw-text
SQL call sites still parse and run under MySQL. The audit:

- `src/master/topics.py:list_recent_topics` — M1 fix is already
  compatible with `ONLY_FULL_GROUP_BY`. Add a MySQL CI test.
- `src/master/main.py:_close_stale_streams` — same.
- `src/master/notes.py` — uses `_dialect.json_array_contains_fragment`;
  already dispatched. Add MySQL coverage.
- `src/master/orchestrate_api.py` — 38 execute call sites, all
  parameterized `text()`. Spot-checked: none use SQLite-only syntax
  that is not already covered by `_dialect`. Add MySQL smoke tests
  for the heaviest 5 handlers.
- `src/master/dispatch.py`, `src/master/event_dispatcher.py`,
  `src/master/mqtt_client.py` — raw text() SQL; grepped for
  `||`, `unixepoch`, `PRAGMA`, `sqlite_master`, `INSERT OR` — all
  clean post-M1.
- `_prebaseline.py` — SQLite-only; guarded by dialect check. No
  change.

#### 13.11 Risks and non-goals

- *Risk.* Managed-MySQL operators may run with `_ai_ci` collation. The
  startup refusal is a hard barrier; the runbook spells out
  `ALTER DATABASE master COLLATE utf8mb4_0900_as_cs;` as the fix. This
  is intentional (see ADR-0020 drivers).
- *Risk / version policy.* MySQL **8.0 reached end-of-life in April
  2026**, so we ship on **`mysql:8.4`** (LTS). The pin is explicit
  (`mysql:8.4`, not `mysql:8` or `latest`) so a tag change cannot
  silently swap us to another major. CI exercises 8.4 **only**; the
  8.0.16–8.0.x window is retained as the documented *floor* for
  managed/external MySQL (the minimum supported server) but is
  "best effort" and unverified post-EOL — if an operator is on 8.0
  and hits a MySQL-8.4-only fix, the remedy is upgrade. 8.4 disables
  `mysql_native_password` by default and uses `caching_sha2_password`;
  pymysql has supported `caching_sha2_password` since 0.9.3, but the
  first full authentication for a given user over a non-TLS
  connection requires the RSA public-key exchange, which needs the
  `cryptography` Python package. We enforce this by adding
  `cryptography` to `requirements.txt` (or pinning it directly if
  it is already a transitive dependency) **and** a CI test that
  creates a fresh user and authenticates it over plain TCP. Do not
  use the removed `--default-authentication-plugin` server flag —
  8.4 replaced it with `authentication_policy`; the image default
  (`caching_sha2_password`) is already correct, so no auth-plugin
  flag appears in compose or CI.
- *Non-goal.* UAT on MySQL. ADR-0019 is explicit; Phase 2 does not
  change it.
- *Non-goal.* Rewriting any call site onto Core expressions beyond
  what the gap list requires. The two-idiom reality from Phase 1
  continues.

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

OQ1–OQ12 all resolved; see the "Resolved questions" section above. OQ10
remains documented there as the replace-only decision.

Phase 2 (M2) items awaiting owner sign-off (introduced rev 5, 2026-10-31):

- [ ] **OQ13 — Collation policy on MySQL.** Recommendation:
  `utf8mb4_0900_as_cs` server-wide, verified at startup, hard refusal
  on mismatch. See §13.4 D1 and ADR-0020. Alternative is accepting the
  MySQL default (`_ai_ci`) and documenting the behaviour change
  (ADR-0020 C4). Owner must sign off before Phase 2 PR opens — architect.
- [ ] **OQ14 — Large-text column classification.** Recommendation: per
  the §13.3 table (TEXT / MEDIUMTEXT / LONGTEXT via
  `Text().with_variant(...)`). Alternative: LONGTEXT everywhere for
  operational simplicity, at the cost of larger row-metadata overhead
  — architect.
- [ ] **OQ15 — Baseline revision strategy.** Recommendation: freeze
  the baseline; land all Phase 2 schema changes in
  `000000000002_mysql_dialect_fixes.py`. CI job
  `baseline-ddl-stability` enforces byte-equal baseline DDL for
  SQLite + Postgres. See §13.4 D5 and ADR-0020 strategy S2 — architect.
- [ ] **OQ16 — `docker-compose.mysql.yml` image pin.** Recommendation:
  **`mysql:8.4`** (LTS) with explicit server flags. MySQL 8.0 is EOL
  (April 2026). pymysql + `caching_sha2_password` works provided
  `cryptography` is in `requirements.txt` (for the first-auth RSA
  key exchange over non-TLS); add a CI fresh-user plain-TCP
  authentication test to gate it. Keep 8.0.16+ as the *documented
  floor* for external/managed MySQL (best-effort; CI tests 8.4 only).
  Do **not** use the removed `--default-authentication-plugin` server
  flag — 8.4 uses `authentication_policy`. See §13.4 D6 — senior-sre.
- [ ] **OQ17 — Required `sql_mode` flags.** Recommendation: refuse to
  start unless `STRICT_TRANS_TABLES`, `ONLY_FULL_GROUP_BY`,
  `NO_ZERO_IN_DATE`, `NO_ZERO_DATE`, `ERROR_FOR_DIVISION_BY_ZERO` are
  set. `PIPES_AS_CONCAT` is not required (we don't use `||`). See
  §13.4 D3 — architect.

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
- *M2 (Phase 2):* MySQL hardening. Sequenced as small, independently
  reviewable commits (full detail: §13; architectural decisions:
  [ADR-0020](../decisions/0020-mysql-support-details.md)).
  **Engineer owns 1–7, 9–11; senior-sre owns 8; tester owns 12.**

  1. *Preflight.* Add `scripts/dump_baseline_ddl.py` and commit the
     `tests/master/db/fixtures/baseline_ddl_{sqlite,postgresql}.sql`
     fixtures. Add a passing `baseline-ddl-stability` test. Lands on
     SQLite + Postgres CI without touching MySQL yet — proves the
     verification mechanism works before we rely on it.
  2. *Startup verification helper.* Add
     `_dialect.verify_mysql_server_setup(conn)` with unit tests via
     mocked connection. Wire it into `startup_db` behind the MySQL
     dialect check. No DDL change.
  3. *Driver integration.* Extend `DBIntegrityError` to include
     `pymysql.err.IntegrityError`. Add a MySQL path to any error
     tests that already cover sqlite3/psycopg equivalents. Also add
     **`cryptography`** to `requirements.txt` (or pin it directly if
     it is already a transitive dependency) — required by pymysql
     for the first-auth RSA public-key exchange against
     `caching_sha2_password` on 8.4 over a non-TLS connection. Add
     a CI job that creates a fresh MySQL user and authenticates it
     over plain TCP so the key-exchange path is covered and we
     cannot silently lose the dependency.
  4. *Alembic revision 000000000002.* Column type widenings per
     §13.2, drop `server_default` on `workspaces.repo_ref` and
     `notes.tags`, promote `chunks.created_at` to
     `Float(precision=53)`, large-text promotions per §13.3. CI's
     `baseline-ddl-stability` keeps SQLite + Postgres baseline byte-
     identical; the SQLite + Postgres suites must pass before MySQL
     gets wired on. Add `upgrade() → downgrade() → upgrade()`
     round-trip test for the revision.
  5. *Generated columns + index replacements* in the same revision
     file (separate commit on the same branch for review ergonomics).
     Four partial indexes → generated TINYINT columns on MySQL only;
     `idx_staffs_scope_name` → generated `scope_id_key` column.
     SQLite / Postgres index shape is preserved via
     `sqlite_where` / `postgresql_where`.
  6. *Call-site code backfill.* `src/master/workspaces.py` and
     `src/master/notes.py` set `repo_ref` / `tags` from Python at
     every insert (compensating for the dropped server defaults).
     Covered by existing unit tests on SQLite + Postgres; MySQL leg
     exercises them end-to-end when it switches on.
  7. *Snapshot and conftest transaction fixes.*
     `src/master/db/snapshot.py:_acquire_truncate_locks_in_txn` grows
     a MySQL branch (`SET SESSION innodb_lock_wait_timeout` +
     `SET SESSION lock_wait_timeout`); the import's truncate phase
     switches from TRUNCATE to DELETE under `SET FOREIGN_KEY_CHECKS=0`.
     `tests/conftest.py:_non_sqlite_truncate` makes the same change.
  8. *Compose overlay.* `docker-compose.mysql.yml` with the
     `db-bundled-mysql` profile; align `docker-compose.ci.yml`
     mysql service flags to the overlay. New `docker-compose.yml`
     entry if any — none expected. `.sre/test-db.sh` already handles
     the mysql leg; verify end-to-end.
  9. *CI enablement.* Remove `if: false` and `continue-on-error`
     from `test-mysql`. Add it to `test-gate.needs`. Add the
     `snapshot-roundtrip-mysql` job needed by §13.9.
  10. *Runbook + config reference.*
      `docs/guides/runbooks/mysql-setup.md` (version floor,
      collation, sql_mode, bundled vs managed), updates to
      `docs/references/config.md` (`DATABASE_URL` MySQL form,
      MySQL-only env vars, `charset=utf8mb4`).
  11. *Release notes.* Entry in `docs/releases/vX.Y.md` for the
      Phase 2 release: MySQL is now CI-blocking, enumerate the
      operator-visible requirements (collation, sql_mode, 8.0.16
      floor), link the runbook.
  12. *Test strategy (owned by tester).* MySQL remains CI-only (no
      UAT). Parity is proved by:
      - *Baseline DDL stability* on every PR (SQLite + Postgres).
      - *The full pytest suite on MySQL* on every PR via
        `.sre/test-db.sh mysql`, blocking via `test-gate`. Any test
        that passes on Postgres but fails on MySQL is a gap and
        gets a MySQL-targeted regression test added before the
        underlying code change is accepted.
      - *Alembic round-trip:* `alembic upgrade head → downgrade -1
        → upgrade head` on all three dialects.
      - *Snapshot round-trip on MySQL:* new CI job seeds a SQLite
        DB, exports a snapshot, imports it into a mysql:8.4
        container, resets `chunks.id`, inserts a new chunks row,
        asserts no PK collision, then reads selected rows back and
        compares byte-for-byte to the SQLite source for a known
        subset (verifies emoji / 4-byte utf8mb4 round-trips).
      - *Startup refusal paths:* unit tests for
        `verify_mysql_server_setup` cover the version-too-low,
        collation-wrong, and sql_mode-missing paths with mocked
        connections. One integration test spins up `mysql:8.4`
        with `--collation-server=utf8mb4_0900_ai_ci` and asserts
        `startup_db` exits non-zero.
      - *Fresh-user `caching_sha2_password` auth over plain TCP:*
        new CI job creates a fresh MySQL user on the 8.4 service,
        connects as that user over a non-TLS TCP connection, and
        asserts success — exercises pymysql's RSA public-key
        exchange path and guards against losing `cryptography`
        from `requirements.txt`.
      - *Generated-column semantics:* targeted test writes a row
        to `workspaces`, archives it, and asserts the unique
        constraint on `name` becomes "name is free" after
        archive — same behaviour as the Postgres partial index.
      - *Collation semantics:* test inserts `"Alice"` and `"alice"`
        into `workspaces.name`; both must succeed on all three
        dialects.
- *M3 (Phase 3, optional):* Cleanup — remove `_prebaseline.py` and
  the `get_connection` shim once call sites are off raw `text()`
  SQL.
