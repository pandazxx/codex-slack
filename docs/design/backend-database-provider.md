# Design: Backend database provider — Postgres first, MySQL second

**Status:** draft
**Author:** architect
**Date:** 2026-10-24
**Related ADRs:** [ADR-0019](../decisions/0019-backend-database-provider.md); supersedes the "SQLite deferred migration" note in [ADR-0005 §4](../decisions/0005-v3-system-architecture.md); respects the single-user plaintext-secret posture of [ADR-0009](../decisions/0009-runtime-configuration-and-staff-system.md) and [ADR-0010](../decisions/0010-workspace-env-var-overrides.md).

## Context

The master service persists state in a single SQLite file. Issue
[#268](https://github.com/pandazxx/codex-slack/issues/268) asks for a
pluggable backend (PostgreSQL first-class, MySQL second), plus data
migration tooling that lets existing SQLite installs cut over without
losing history, plus compose changes to run the chosen backend
alongside the stack.

Today's reality (surveyed 2026-10-24):

- `src/master/db.py` holds a ~670-line `_SCHEMA` string, a 28-entry
  `_MIGRATIONS` list of `ALTER TABLE ADD COLUMN` statements executed
  with "swallow OperationalError" semantics, and seven table-rebuild
  helpers that handle CHECK-constraint and uniqueness-constraint
  changes (SQLite cannot `ALTER CONSTRAINT`).
- Direct `sqlite3` import or `get_connection(db_path)` calls live in
  `db.py`, `main.py`, `mqtt_client.py`, `notify.py`, `topics.py`,
  `messages.py`, `notes.py`, `topic_export.py`, `event_actions.py`,
  `orchestrate_api.py`, `event_dispatcher.py`, `staffs.py`,
  `dispatch.py`, `runtime_config.py`, `workspaces.py` (18 files
  including tests).
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
  - Table-recreation migration pattern for CHECK-constraint changes
- Datetimes are stored as ISO-8601 TEXT (`YYYY-MM-DDTHH:MM:SSZ`) —
  already dialect-neutral. The one float column
  (`chunks.created_at REAL`) is the exception.
- Short-lived connections per request (`get_connection(db_path)`
  called inside each FastAPI handler) is the dominant pattern.
  Background threads open their own. There is no pool.

## Goals

- One configuration surface for picking the DB backend:
  `DATABASE_URL` (SQLAlchemy URL format). SQLite remains default.
- Postgres is a *first-class* production target in phase 1: supported
  end-to-end (schema, migrations, deploy, backup, tests, docs).
- MySQL is a *supported* production target in phase 2 with a documented
  minimum version floor (MySQL 8.0.13+) so partial-index / functional-index /
  enforced-CHECK semantics are available.
- Snapshot tooling exports a provider-neutral representation of the
  current DB and imports it into a fresh target DB of any supported
  provider.
- CI runs the master test suite against SQLite on every PR and against
  Postgres at least on `main` / nightly (final cadence per Open Question 5).
- Operators get a one-page runbook for migrating an existing SQLite
  install to Postgres with downtime bounded by snapshot size.

## Non-Goals

- *Not* switching to async DB. Master mixes sync FastAPI handlers,
  background threads, and `asyncio.to_thread` bridges. Async SQLAlchemy
  across that surface is a separate design.
- *Not* splitting read/write replicas, adding connection-level sharding,
  or supporting multi-master.
- *Not* changing attachment storage (filesystem under
  `MASTER_DATA_DIR/attachments` per ADR-0007). Blobs stay out of the DB.
- *Not* adding encryption at rest. ADR-0009's plaintext-secret posture
  carries over; moving to Postgres does not make it worse as long as
  the connection is loopback or a trusted LAN segment (documented).
- *Not* porting the agent worker or the notes MCP server to the new
  provider abstraction. Both are stateless with respect to the master DB
  and talk via MQTT / HTTP.
- *Not* a full-ORM rewrite. ADR-0019 chose SQLAlchemy Core + Alembic
  explicitly to avoid that.

## Proposed Design

### 1. Abstraction layer

A new `src/master/db/__init__.py` package replaces today's
`src/master/db.py` module. The public surface stays small so that
existing call sites change in one mechanical pass:

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
  smallest-possible change for the ten modules that already use
  `get_connection`.
- Low-level helpers that depend on SQLite-only syntax
  (`PRAGMA foreign_keys`, `sqlite_master` lookups) move into
  dialect-dispatched private helpers (`_dialect.pg_insert_or_ignore`,
  `_dialect.now_literal`, `_dialect.table_exists`, etc.).

### 2. Schema and migration management

Alembic owns schema evolution end-to-end. The in-code `_SCHEMA`
string and `_MIGRATIONS` list in `db.py` go away in phase 1.

Layout:

```
src/master/db/
  __init__.py           # public engine/get_connection
  _dialect.py           # upsert, now(), introspection shims
  _row.py               # mapping wrapper for sqlite3.Row compatibility
alembic.ini
alembic/
  env.py                # loads DATABASE_URL from master settings
  versions/
    000000000001_baseline.py   # creates all tables as they stand on PR merge
    000000000002_...py         # future migrations live here
```

Rules:

- *Baseline revision* reflects the current schema as a single
  Alembic revision. Any schema change after merge adds a new
  revision — no more `_MIGRATIONS` list, no more
  `swallow OperationalError if column already exists`.
- *Existing SQLite installs* upgrade in-place: `alembic stamp` is
  issued once at first startup after the upgrade (when
  `alembic_version` table does not exist but `workspaces` does), to
  mark the baseline as applied. Dispositional in-code migrations
  from the pre-ADR era (envelope backfill, agents→staffs rename,
  event_actions_v2/v3, staffs_session_scope_none,
  staged_dispatches) are **preserved** in `db.py` as one-shot
  "pre-baseline" fixups that run before Alembic takes over. This
  avoids forcing operators onto the snapshot-and-reload path for a
  simple version bump.
- *Fresh installs on any provider* get the baseline straight from
  Alembic — the pre-baseline fixups are no-ops because the
  conditional guards (`sqlite_master` lookup, `IS NULL` probes)
  short-circuit on empty tables. For non-SQLite dialects the fixups
  are skipped entirely (dialect check); they were never needed
  there.
- *Autogenerate* is enabled but not trusted blindly: CHECK
  constraints, partial indexes, and expression indexes are
  hand-written in each revision with a note referencing the
  behaviour under each dialect.

### 3. Dialect handling

Three specific SQLite-only patterns need concrete treatment:

| Pattern | SQLite | Postgres | MySQL 8.0.13+ |
|---|---|---|---|
| Upsert | `INSERT OR IGNORE` | `INSERT ... ON CONFLICT DO NOTHING` | `INSERT IGNORE INTO` |
| Upsert with update | `ON CONFLICT (…) DO UPDATE SET x = excluded.x` | identical | `ON DUPLICATE KEY UPDATE x = VALUES(x)` |
| "Now (sub-second)" default | `unixepoch('now','subsec')` | `extract(epoch from now())` | `UNIX_TIMESTAMP(NOW(3))` |
| Partial index | `CREATE INDEX ... WHERE ...` | identical | functional index via generated column |
| Expression unique | `CREATE UNIQUE INDEX ... ON t(COALESCE(c, ''))` | identical | functional index |
| CHECK constraint | enforced | enforced | enforced (8.0.16+) |
| Introspection | `sqlite_master` / `PRAGMA table_info` | `pg_catalog` / `information_schema` | `information_schema` |

The upsert helper is a single function `upsert(table, values, conflict_cols, update_cols=None)` in `_dialect.py`; call sites that today write
raw `INSERT OR IGNORE` SQL change to it. For `chunks.created_at`, the
default moves off `unixepoch` into Python code (every row that writes
chunks already has a timestamp available), closing one dialect gap
without a dialect helper.

For MySQL, two of the partial indexes become functional indexes over a
generated `IS NULL` column; the design doc for phase 2 (added when phase
1 lands) will enumerate each one.

### 4. Configuration surface

Add one env var, read in `src/master/config.py`:

| Key | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | No | `sqlite+pysqlite:///${MASTER_DATA_DIR}/master_data.db` | SQLAlchemy URL. Supported schemes: `sqlite+pysqlite`, `postgresql+psycopg`, `mysql+pymysql`. |
| `DB_POOL_SIZE` | No | `5` | SQLAlchemy `pool_size`. SQLite ignores (`StaticPool`). |
| `DB_POOL_MAX_OVERFLOW` | No | `10` | SQLAlchemy `max_overflow`. SQLite ignores. |
| `DB_ECHO_SQL` | No | `false` | Dev-only flag to log every statement; off in prod. |

`MASTER_DATA_DIR` keeps its current role (attachments, snapshots), and
the derived `master_data.db` path only matters when `DATABASE_URL` is
the SQLite default. Config reference in `docs/references/config.md`
grows a row per key.

### 5. Snapshot format and tooling

A new CLI, `python -m src.master.db_cli snapshot`, with two
sub-commands:

```
python -m src.master.db_cli snapshot export \
    --source-url "$DATABASE_URL" \
    --out /path/to/snapshot_YYYYMMDDTHHMMSSZ/

python -m src.master.db_cli snapshot import \
    --in  /path/to/snapshot_YYYYMMDDTHHMMSSZ/ \
    --target-url "postgresql+psycopg://user:pw@host:5432/db" \
    [--yes]  # skip the "target already has rows" confirmation
```

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

Rules:

- One JSON object per line; values are dialect-neutral (ISO-8601 TEXT
  dates, JSON for already-JSON columns, base64 for any future bytes).
  `chunks.created_at` is a float on disk; the exporter normalises it
  to a float field in JSON regardless of source dialect.
- Export is streaming (`SELECT` with server-side cursor on Postgres,
  no `fetchall()` of large tables). Import is batched (`executemany`
  of 500 rows) inside a single transaction per table.
- Import *always targets an empty DB*: it runs `alembic upgrade head`
  first, then refuses to proceed if any user-data table has rows,
  unless `--yes` is passed. We do not merge; we restore.
- `schema_version` in the manifest must equal the current Alembic
  head on import, or the tool refuses to proceed. This fails fast on
  cross-release snapshots rather than silently losing columns.
- Row-level foreign keys are respected by load order (parents first).
  The load order is baked into the exporter.
- Round-trip test: a CI job seeds a SQLite DB, exports, imports into
  a Postgres container, and runs a read-side smoke test
  (count-per-table, selected row equality).

### 6. Compose and runtime changes

Three compose files change:

- `docker-compose.yml` — base. No DB service added here; the base
  stays provider-neutral. Reads `DATABASE_URL` from env (empty →
  SQLite default inside the volume).
- `docker-compose.postgres.yml` (new) — optional overlay that adds a
  `postgres` service pinned to `postgres:17-alpine`, a
  `postgres_data` named volume, healthchecked, on the `internal`
  network. Sets `DATABASE_URL=postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master`
  on the master service. Documented as "layer this on
  `docker-compose.yml` for dev/staging/prod when Postgres is the
  chosen provider."
- `docker-compose.mysql.yml` (new, phase 2) — same shape with
  `mysql:8.4`.

The deploy overlay (`docker-compose.deploy.yml`) does not change —
the singleton stays provider-neutral; operators pick a DB overlay via
the justfile deploy recipe (see Open Question 7 for the deploy UX).

### 7. CI test matrix

Phase 1:

```
jobs:
  test-sqlite:   DATABASE_URL=sqlite+pysqlite:///...   # every PR
  test-postgres: DATABASE_URL=postgresql+psycopg://... # every PR (or main-only per OQ5)
  snapshot-roundtrip: SQLite → export → import → PG smoke
```

Phase 2 adds `test-mysql`. The Postgres and MySQL legs use the
service-containers feature of the chosen CI runner so no new SRE
state is needed.

### 8. Phased rollout

**Phase 0 — Groundwork (one PR, no behaviour change)**

- Add SQLAlchemy + Alembic to `requirements.txt`.
- Introduce `src/master/db/` package with `engine()` and a backward-
  compatible `get_connection()` shim built on SQLAlchemy's SQLite
  dialect. All call sites continue to use raw SQL strings via
  `text()`; results are mapping-like via `.mappings().all()`.
- Freeze the current schema into `alembic/versions/000000000001_baseline.py`.
  Add the pre-baseline fixup block for in-place upgrades of existing
  SQLite installs.
- Add `scripts/db_migrate.sh` wrapping `alembic upgrade head`.
- Lint rule: no new `import sqlite3` outside `src/master/db/`,
  `src/master/db_cli.py`, and the test suite.

**Phase 1 — Postgres first-class**

- Convert all SQLite-only SQL at call sites to the dialect helpers
  (`upsert`, timestamp defaults in Python, PRAGMAs replaced by
  SQLAlchemy events for FK enforcement where supported).
- Replace `_MIGRATIONS` list and table-rebuild helpers with Alembic
  revisions. Delete the fixup block once a release has shipped that
  stamps the baseline on first boot (one-release deprecation).
- Ship the snapshot CLI (export + import + round-trip test).
- Ship `docker-compose.postgres.yml` and the migrate-from-SQLite
  runbook.
- Expand CI matrix to SQLite + Postgres.
- Update `docs/references/config.md` with `DATABASE_URL` and pool
  tunables; document the SQLite→Postgres migration in
  `docs/guides/runbooks/migrate-sqlite-to-postgres.md`.

**Phase 2 — MySQL second**

- Add MySQL dialect: adapt the two partial indexes to functional
  indexes over generated columns; verify CHECK enforcement under
  MySQL 8.0.16+.
- Ship `docker-compose.mysql.yml`.
- Expand CI matrix to include MySQL (per-PR or nightly, decided when
  phase 1 lands).
- Update docs with the MySQL version floor and known caveats.

**Phase 3 — Cleanup (optional, not blocking)**

- Delete the pre-baseline fixups.
- Delete the `get_connection()` shim once all call sites are moved
  to Core expression API (long tail).

## Alternatives Considered

- **Thin DIY dialect-abstraction layer.** Rejected in ADR-0019. Would
  keep raw SQL in-place but re-invent half of SQLAlchemy's dialect
  module and leave migrations hand-written per provider.
- **Full ORM rewrite.** Rejected in ADR-0019. Too large a diff for
  this issue; mixing async FastAPI handlers with sync workers via
  async SQLAlchemy adds a separate design problem.
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

## Open Questions

- [ ] **OQ1 — Scope of this change: master DB only, or master + any
      other persistent store?** The agent worker and the notes MCP
      server don't persist anything in the master DB today. Confirm
      we are scoping to master only. — Owner: project-owner.
- [ ] **OQ2 — SQLite as the dev-stack default forever, or move dev to
      Postgres for parity with prod?** Keeping SQLite is lower
      friction for contributors; moving dev to Postgres catches
      dialect bugs earlier. Recommendation: keep SQLite as the test
      default, add an opt-in `docker-compose.postgres.yml` overlay
      that `just dev-up` can be told to use. — Owner: project-owner
      + sre.
- [ ] **OQ3 — Snapshot format: JSONL per table (recommended) or raw
      `pg_dump`-compatible SQL?** JSONL wins on cross-provider
      portability; raw SQL is slightly faster. We recommend JSONL
      and only revisit if snapshot time becomes a bottleneck. —
      Owner: project-owner.
- [ ] **OQ4 — Migration triggering: run `alembic upgrade head`
      automatically on master startup, or require an explicit
      `scripts/db_migrate.sh` step before deploy?** Auto-upgrade is
      friendlier for self-hosted operators; explicit is safer for
      the (hypothetical future) multi-replica case. Recommendation:
      auto-upgrade in the master lifespan, gated by
      `DB_AUTO_MIGRATE=true` (default on, off-switchable). — Owner:
      sre.
- [ ] **OQ5 — CI matrix cadence: run the Postgres leg on every PR,
      or only on `main` / nightly?** Every-PR catches dialect bugs
      early but costs ~2–3 CI minutes per PR. Recommendation:
      every-PR for the first two releases, revisit if cost hurts.
      — Owner: sre.
- [ ] **OQ6 — MySQL version floor.** 8.0.13 unlocks functional
      indexes; 8.0.16 enforces CHECK. Recommendation: 8.0.16+ and
      document it as a hard requirement, refuse to start otherwise.
      — Owner: project-owner.
- [ ] **OQ7 — Deploy UX for Postgres.** Options: (a) Postgres as a
      sibling service in the singleton compose, (b) assume an
      external managed DB with `DATABASE_URL` pointed at it,
      (c) both, documented. Recommendation: (c) — ship the compose
      service for self-hosted installs, document external-managed
      as the "scale up" path. — Owner: sre.
- [ ] **OQ8 — Secrets at rest.** ADR-0009 established "plaintext in
      SQLite is acceptable for the single-user threat model." Moving
      to a server-based DB means secrets may now cross a wire.
      Confirm: do we require TLS to the DB (even on loopback), or do
      we accept plaintext for the self-hosted local-DB case and
      document TLS as required only for remote DB? — Owner:
      project-owner.
- [ ] **OQ9 — Snapshot CLI location and name.** Options:
      `python -m src.master.db_cli snapshot ...` (recommended),
      `scripts/db_snapshot.py`, or extending the existing
      `src/master/cli.py`. — Owner: engineer.
- [ ] **OQ10 — Import semantics: replace-only, or allow
      append/merge?** Replace-only is simpler and matches the
      "migrate to new provider" use case; append/merge opens the
      door to data-loss foot-guns. Recommendation: replace-only in
      v1, revisit on real demand. — Owner: project-owner.
- [ ] **OQ11 — Pre-baseline fixups: how long do we keep them?**
      Existing SQLite installs with the envelope backfill /
      agents→staffs / event_actions_v2+v3 /
      staffs_session_scope_none migrations partially applied need
      the current `db.py` helpers to run once more before Alembic
      takes over. Recommendation: ship them for exactly one release
      cycle after phase 1 lands, then delete. — Owner:
      project-owner.
- [ ] **OQ12 — Snapshot forward/backward compatibility.** Can a
      snapshot taken against schema version `N` be imported into a
      master running schema version `N+1`? Recommendation: no —
      versions must match exactly; cross-release migrations go
      through "upgrade source first, then snapshot, then import."
      This keeps the tool simple. — Owner: project-owner.

## Implementation Plan

Milestones map 1:1 to the phases above. Each phase is a separate PR
(or small series) and ends with a release:

- *M0 (Phase 0):* Groundwork PR — SQLAlchemy/Alembic in, baseline
  revision, shim in place, all tests green on SQLite. No
  user-visible change.
- *M1 (Phase 1):* Postgres first-class — call-site conversion,
  Alembic migrations replacing `_MIGRATIONS`, snapshot CLI,
  Postgres compose overlay, runbook, CI matrix expansion. Release
  as the first version supporting a non-SQLite backend.
- *M2 (Phase 2):* MySQL support — dialect-specific index handling,
  MySQL compose overlay, CI leg, docs.
- *M3 (Phase 3, optional):* Cleanup — remove pre-baseline fixups
  and the `get_connection` shim once call sites are off raw
  `text()` SQL.
