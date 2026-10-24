# Test Plan: Backend Database Provider

**Feature:** Backend database provider — Postgres default, SQLite and MySQL supported
**Design doc:** [docs/design/backend-database-provider.md](../design/backend-database-provider.md)
**ADR:** [ADR-0019](../decisions/0019-backend-database-provider.md)
**Issue:** [#268](https://github.com/pandazxx/codex-slack/issues/268)
**Branch:** `feat/db-provider-phase0`

---

## Scope

This test plan covers all phases (M0–M3) of the backend database provider
feature. M0 is covered in full detail below. Later phases are covered at
high level; their test plans will be refined as implementation progresses.

**Out of scope for UAT (all phases):** SQLite and MySQL. Per the project
owner, UAT targets Postgres from M1 on. SQLite and MySQL are verified by
unit/CI tests only.

---

## M0 — Groundwork (this PR)

### Overview

M0 has no user-visible change. It restructures `src/master/db.py` into
`src/master/db/`, converts qmark `?` placeholders to `text()` with named
`:param` bindings, funnels direct `sqlite3.connect()` callers through the
`get_connection()` shim, adds an Alembic baseline revision, and adds
`scripts/db.sh` scaffolding. Startup still uses the legacy SQLite `init_db()`
path. SQLite is still the default `DATABASE_URL`.

### M0 UAT

M0 has no user-visible change. Its UAT is a no-regression smoke test:
the existing test suite must remain green on SQLite after all M0 changes.
No UAT cases require human interaction in M0.

---

## Test Cases

### TC-01: Baseline parity — `init_db()` vs `alembic upgrade head`

**Phase:** M0
**Automated:** automated
**Priority:** P0 (key M0 test)

Create a fresh SQLite DB via the legacy `init_db()` path. Create a second
fresh SQLite DB via `alembic upgrade head` against a `sqlite+pysqlite:///`
temporary URL. Compare both with SQLAlchemy `inspect()`:

- Same set of table names (`TABLES` constant).
- For each table: same column names and same affinity-normalised column types.
- For each column: same nullability, same primary-key membership.
- Foreign-key constraints present and pointing to the correct parent table/column.
- `UNIQUE` constraints present on the correct column sets.
- Regular indexes present by name (or by covered columns where names may differ).
- Partial indexes present with correct `WHERE` expression (where inspectable).
- `INTEGER PRIMARY KEY AUTOINCREMENT` on `chunks.id` (maps to `IDENTITY` in
  Alembic/Core; inspect confirms `autoincrement=True`).

**Pass criteria:** Both schemas are structurally identical. Zero drift between
the legacy path and the Alembic baseline.

**Skip condition:** `src.master.db.engine` not importable (db/ package not
yet created) or `alembic/versions/000000000001_baseline.py` does not exist.

---

### TC-02: Shim — `text()` with named params via `get_connection()`

**Phase:** M0
**Automated:** automated

After M0, `get_connection()` returns a shim that accepts `text()` objects
with named `:param` bindings and a dict of values.

Sub-cases:
- `conn.execute(text("SELECT 1"))` works and returns a result.
- `conn.execute(text("INSERT INTO ... VALUES (:id)"), {"id": "x"})` inserts a row.
- `conn.execute(text("SELECT id FROM workspaces WHERE id = :id"), {"id": "x"}).fetchone()["id"] == "x"`.
- `conn.execute(...).fetchall()` returns a list.
- Row objects support name-keyed access (`row["column_name"]`).

**Pass criteria:** All sub-cases pass without raising.

**Skip condition:** `src.master.db.engine` not importable (db/ package not
yet created).

---

### TC-03: Shim — context manager, commit, rollback

**Phase:** M0
**Automated:** automated

- `with get_connection() as conn:` enters and exits cleanly.
- Rows inserted inside the `with` block are committed on clean exit.
- `conn.rollback()` discards uncommitted inserts.

**Pass criteria:** Context manager and transaction methods behave as
documented.

**Skip condition:** `src.master.db.engine` not importable.

---

### TC-04: Shim — foreign-key enforcement on SQLite

**Phase:** M0
**Automated:** automated

Inserting a row that violates a foreign-key constraint through
`get_connection()` raises an integrity error (foreign-key enforcement must
be active on SQLite, equivalent to the legacy `PRAGMA foreign_keys = ON`).

**Pass criteria:** `IntegrityError` (or equivalent) raised on FK violation.

**Skip condition:** `src.master.db.engine` not importable.

---

### TC-05: `DATABASE_URL` resolution — default SQLite

**Phase:** M0
**Automated:** automated

When `DATABASE_URL` is not set in the environment, `engine()` must resolve
to a SQLite URL pointing at the expected default path under `MASTER_DATA_DIR`
(or `:memory:` in the test override). The engine dialect name is `sqlite`.

**Pass criteria:** `engine().dialect.name == "sqlite"` with the default URL.

**Skip condition:** `src.master.db.engine` not importable.

---

### TC-06: `DATABASE_URL` resolution — override

**Phase:** M0
**Automated:** automated

When `DATABASE_URL` is set to `sqlite+pysqlite:///path/to/test.db`,
`engine()` creates an engine whose URL matches the override.

**Pass criteria:** `engine().url` reflects the override.

**Skip condition:** `src.master.db.engine` not importable.

---

### TC-07: Lint guard — no `import sqlite3` outside `src/master/db/`

**Phase:** M0
**Automated:** automated

After M0, `import sqlite3` must not appear in any `.py` file under `src/`
except:
- `src/master/db/` (the package itself, including `_prebaseline.py`)
- `src/master/db_cli.py`

The test uses `ast.parse` on every `.py` file in `src/` and asserts zero
violations.

**Pass criteria:** No violations found.

**Skip condition (pre-M0):** Test is skipped explicitly with message
"sqlite3 imports outside db/ are expected pre-M0; skip until M0 merges".
The skip is removed once the engineer confirms M0 implementation is complete.

---

### TC-08: `scripts/db.sh --help` exits 0 and lists subcommands

**Phase:** M0
**Automated:** automated

`bash scripts/db.sh --help` exits with code 0. The output contains the
strings `migrate`, `verify-schema`, and `snapshot-and-migrate`.

**Pass criteria:** Exit code 0, all three subcommands mentioned in output.

**Skip condition:** `scripts/db.sh` does not exist.

---

### TC-09: `scripts/db.sh` unknown subcommand exits non-zero

**Phase:** M0
**Automated:** automated

`bash scripts/db.sh this-subcommand-does-not-exist` exits with a non-zero
exit code.

**Pass criteria:** Exit code != 0.

**Skip condition:** `scripts/db.sh` does not exist.

---

### TC-10: `scripts/db.sh migrate` creates `alembic_version` at baseline head

**Phase:** M0
**Automated:** automated

Set `DATABASE_URL` to a temporary SQLite file, run
`bash scripts/db.sh migrate`, then connect to the file and assert that the
`alembic_version` table exists and contains exactly one row whose
`version_num` equals the expected baseline revision id
(`000000000001`).

**Pass criteria:** `alembic_version` table exists with one row at the
baseline revision.

**Skip condition:** `scripts/db.sh` does not exist or `alembic` package not
installed.

---

### TC-11 (optional): Postgres baseline — skip unless `TEST_POSTGRES_URL` set

**Phase:** M0 (scaffolding for M1 CI matrix)
**Automated:** automated (skipped in CI until M1)

Run `alembic upgrade head` against `TEST_POSTGRES_URL` (if set), then
inspect all tables and assert they match the expected baseline schema.

**Pass criteria:** All tables present, columns correct.

**Skip condition:** `TEST_POSTGRES_URL` env var not set.

---

### TC-12 (optional): MySQL baseline — skip unless `TEST_MYSQL_URL` set

**Phase:** M0 (scaffolding for M1/M2 CI matrix)
**Automated:** automated (skipped in CI until M2)

Run `alembic upgrade head` against `TEST_MYSQL_URL` (if set), then
inspect all tables and assert they match the expected baseline schema
(with MySQL affinity normalisation applied).

**Pass criteria:** All tables present, columns correct.

**Skip condition:** `TEST_MYSQL_URL` env var not set.

---

## M1 — Postgres Default (future)

High-level test areas (details TBD at authoring time for M1 PR):

- Startup with `DATABASE_URL` unset and no existing SQLite file: connects to
  bundled Postgres, runs `alembic upgrade head` on empty DB, starts normally.
- Startup with `DATABASE_URL` unset and existing non-empty SQLite file: refuses
  to start, emits the correct error message pointing at the migration runbook.
- Startup with DB behind code version: refuses to start, emits the correct
  error message pointing at `scripts/db.sh migrate`.
- Startup with DB at correct version: starts normally.
- Startup with DB ahead of code version: refuses to start.
- `scripts/db.sh snapshot export` produces a valid manifest and JSONL files for
  every table.
- `scripts/db.sh snapshot import` loads JSONL into a fresh Postgres DB and
  resets sequences so subsequent inserts do not collide.
- Snapshot round-trip: seed SQLite, export, import into Postgres, verify
  row counts and sampled row equality.
- Sequence reset: after import, an INSERT into `chunks` succeeds without PK
  collision.
- Dialect helpers: `upsert()` with `conflict_cols` produces `ON CONFLICT DO
  NOTHING` on Postgres and `INSERT IGNORE` on MySQL (unit-tested).

UAT: all M1 UAT cases target Postgres on the staging singleton.

---

## M2 — MySQL Hardened (future)

High-level test areas:

- Partial indexes represented as functional indexes over generated columns:
  CI MySQL run confirms the baseline applies without error on MySQL 8.0.16+.
- VARCHAR(36) widths on UUID PK/FK/indexed columns: inspect confirms width
  on MySQL.
- MySQL version floor: master refuses to start on MySQL < 8.0.16.

UAT: MySQL is CI-only, no UAT leg.

---

## M3 — Cleanup (future)

- `_prebaseline.py` deleted: existing tests confirm no regression.
- `get_connection()` shim removed: all call sites use Core expression API;
  import of the shim raises `ImportError`.

---

## Non-functional Requirements

- **Performance:** `get_connection()` must not add measurable overhead on
  the SQLite path vs the legacy `sqlite3.connect()` call. No explicit
  benchmark test; a 10x regression would be detectable in the existing
  integration test suite's wall-clock time.
- **Thread safety:** `engine()` returns a module-level singleton. Multiple
  threads calling `get_connection()` concurrently must not raise. (Tested
  implicitly by the existing multi-threaded test suite under M0; explicit
  concurrency test deferred to M1 when pool matters.)
- **Error messages:** Version-mismatch messages at startup must contain both
  the DB version and the code version, and must mention the remediation
  command. (Covered by M1 startup tests.)

---

## System Interface Assertions

| Interface | What to assert | Phase |
|---|---|---|
| `src.master.db.engine()` | Returns SQLAlchemy Engine; dialect matches DATABASE_URL | M0 |
| `src.master.db.get_connection()` | Returns shim accepting `text()` + named params; row["col"] works | M0 |
| `src.master.db.init_db()` | Signature unchanged (db_path: str); creates all TABLES | M0 |
| `alembic/versions/000000000001_baseline.py` | File exists; `alembic upgrade head` applies cleanly to SQLite | M0 |
| `scripts/db.sh` | `--help` exits 0; unknown subcommand exits non-zero | M0 |
| `scripts/db.sh migrate` | Creates `alembic_version` at baseline rev | M0 |
| `DATABASE_URL` env var | Overrides default URL; default is SQLite in M0 | M0 |
| `import sqlite3` outside db/ | Zero occurrences post-M0 | M0 |
