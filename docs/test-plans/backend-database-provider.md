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

## M1 — Postgres Default

**Branch:** `feat/db-provider-m1`
**Design doc refs:** §5 (migration triggering), §8 (snapshot), §9 (compose), §10 (CI), §11 (SQLite safety), §12 (Phase 1 carried items)

UAT runs on the staging singleton with bundled Postgres (`COMPOSE_PROFILES=db-bundled`).
SQLite and MySQL are verified by unit/CI tests only.

---

### TC-13: Empty DB → auto-upgrade on startup

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_startup.py::TestEmptyDbAutoUpgrade`

`startup_db(url)` on an empty (no-tables) SQLite file runs `alembic upgrade head`
automatically without operator action.

Sub-cases:
- exits normally (no `SystemExit`)
- creates every table in `TABLES`
- `alembic_version` is set to the current alembic head revision

Cross-dialect: also runs against `TEST_POSTGRES_URL` (skip if unset).

**Pass criteria:** all tables present, `alembic_version` at HEAD, no exception raised.

**Skip condition:** `src.master.db.startup_db` not available (M1 not implemented).

---

### TC-14: Pre-baseline SQLite DB → fixups + stamp, rows preserved

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_startup.py::TestPreBaselineSqlite`

A DB created via the legacy `init_db_sqlite()` path (schema present, no
`alembic_version`) gets pre-baseline fixups applied and is then stamped at
the baseline revision. Existing rows are not deleted.

Sub-cases:
- `alembic_version` is created with a revision starting `000000000001`
- a workspace row inserted before `startup_db` is still present after

**Pass criteria:** stamp applied; existing rows preserved; no exception raised.

**Skip condition:** `src.master.db.startup_db` not available.

---

### TC-15: Up-to-date DB → no-op

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_startup.py::TestUpToDateNoOp`

A second call to `startup_db(url)` on a DB already at HEAD returns normally
without modifying `alembic_version`.

**Pass criteria:** `alembic_version` unchanged; no exception raised.

**Skip condition:** `src.master.db.startup_db` not available.

---

### TC-16: Behind DB → refuses, message, DB untouched

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_startup.py::TestBehindRefuses`

When `alembic_version` is set to an older-than-HEAD revision, `startup_db` must:
- exit non-zero (`SystemExit(1)`)
- emit a message containing `"behind"`, `"scripts/db.sh migrate"`, and
  `"scripts/db.sh snapshot-and-migrate"` (the §5 prescribed message)
- leave `alembic_version` unchanged (do not touch the DB)

Cross-dialect: also runs against `TEST_POSTGRES_URL` (skip if unset).

**Pass criteria:** non-zero exit, correct message keywords, `alembic_version` unchanged.

**Skip condition:** `src.master.db.startup_db` not available.

---

### TC-17: Ahead DB → refuses

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_startup.py::TestAheadRefuses`

When `alembic_version` is set to an unknown future revision, `startup_db` must:
- exit non-zero
- emit a message containing at least one of `"ahead"`, `"newer"`, `"unknown"`,
  or `"downgrade"`
- leave `alembic_version` unchanged

Cross-dialect: also runs against `TEST_POSTGRES_URL` (skip if unset).

**Pass criteria:** non-zero exit, appropriate message keyword, DB untouched.

**Skip condition:** `src.master.db.startup_db` not available.

---

### TC-18: Cross-dialect startup state machine

**Phase:** M1 (CI matrix — parametrized in TC-13 through TC-17)
**Automated:** automated (skipped when `TEST_POSTGRES_URL` / `TEST_MYSQL_URL` unset)

Same state-machine tests run against each database dialect. Postgres is
the primary gate; MySQL is gated by `TEST_MYSQL_URL`.

**Pass criteria:** all state-machine outcomes correct on each dialect.

**Skip condition:** dialect env var not set.

---

### TC-19: Snapshot export — manifest and JSONL layout

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_snapshot.py::TestSnapshotExport`

`scripts/db.sh snapshot export --source-url URL --out DIR` produces:
- `manifest.json` with `schema_version` == alembic head, `created_at`, `source_dialect`, `tables`
- one `{table}.jsonl` for every table in `TABLES`
- `manifest.tables[t].rows` matches seeded row count
- `manifest.tables[t].sha256` matches `sha256(t.jsonl)`
- boolean columns encoded as JSON `true`/`false` (not `0`/`1`)
- NULL columns encoded as JSON `null`
- JSON columns embedded as objects/arrays (not strings)
- Unicode/emoji present without corruption
- REAL values encoded as JSON floats

**Pass criteria:** all sub-cases pass.

**Skip condition:** `scripts/db.sh snapshot export --help` exits non-zero (M1 not implemented).

---

### TC-20: Snapshot import round-trip — every row matches

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_snapshot.py::TestSnapshotImportRoundtrip`

`scripts/db.sh snapshot import --in DIR --target-url URL` into a fresh DB
(after `alembic upgrade head`) reproduces every seeded row exactly.

Sub-cases:
- exit 0
- row counts match manifest
- unicode/emoji values round-trip
- NULL values are NULL in target
- boolean values are correct in target
- REAL values do not drift (tolerance < 1e-9)

**Pass criteria:** all sub-cases pass.

**Skip condition:** snapshot CLI not available.

---

### TC-21: Sequence reset — INSERT after import succeeds

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_snapshot.py::TestSequenceResetAfterImport`

After importing a snapshot that contains `chunks` rows, inserting a new `chunks`
row without specifying `id` succeeds without a primary-key collision. Covers
`_dialect.reset_sequences_for_table` (§8).

Sub-cases:
- single INSERT succeeds
- bulk INSERT (5 rows) succeeds

**Pass criteria:** no `IntegrityError` or sequence conflict.

**Skip condition:** snapshot CLI not available.

---

### TC-22: Import guards — non-empty target, schema mismatch, checksum

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_snapshot.py::TestImportGuards`

Sub-cases:
- import into non-empty target **without** `--yes` → exit non-zero
- import into non-empty target **with** `--yes` → exit 0, rows replaced
- `manifest.schema_version` != alembic head → exit non-zero
- JSONL file content modified (checksum mismatch) → exit non-zero

**Pass criteria:** all guards fire correctly.

**Skip condition:** snapshot CLI not available.

---

### TC-23: SQLite → Postgres snapshot round-trip

**Phase:** M1 (CI `snapshot-roundtrip` job)
**Automated:** automated (skipped when `TEST_POSTGRES_URL` unset)
**File:** `tests/master/db/test_m1_snapshot.py::TestSqliteToPostgresRoundtrip`

Full cross-dialect round-trip: seed SQLite → export → import into Postgres → verify
row counts and INSERT without PK collision.

**Pass criteria:** row counts match; POST-import INSERT succeeds.

**Skip condition:** `TEST_POSTGRES_URL` not set.

---

### TC-24: Safety net — SQLite file + unset DATABASE_URL refuses

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_safety_net.py::TestSafetyNetRefusesWithSqliteFile`

When `DATABASE_URL` is unset and `${MASTER_DATA_DIR}/master_data.db` exists with
a `workspaces` table, `sqlite_safety_check(data_dir)` raises `SystemExit(1)`.

The error message must contain:
- the full path to `master_data.db`
- `"(a)"` (Keep SQLite option)
- `"(b)"` (Migrate to Postgres option)
- `"DATABASE_URL"` (what the operator needs to set)

Sub-cases also verify:
- a zero-byte file does NOT trigger the safety net
- a file WITHOUT a `workspaces` table does NOT trigger

**Pass criteria:** non-zero exit; all four message substrings present.

**Skip condition:** `src.master.db.sqlite_safety_check` not available.

---

### TC-25: Safety net — no SQLite file → Postgres default URL

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_safety_net.py::TestSafetyNetNoSqliteFile`

When `DATABASE_URL` is unset and no `master_data.db` exists in `MASTER_DATA_DIR`,
`sqlite_safety_check` does not raise and `resolve_db_url()` returns a URL starting
with `postgresql+psycopg://`.

**Pass criteria:** no exception; URL starts with `postgresql+psycopg://`.

**Skip condition (partial):** The `resolve_db_url` sub-case skips if it still returns
a SQLite URL (M1 Postgres default not yet switched).

---

### TC-26: Safety net — explicit DATABASE_URL always wins

**Phase:** M1
**Automated:** automated
**File:** `tests/master/db/test_m1_safety_net.py::TestSafetyNetExplicitUrlWins`

When `DATABASE_URL` is set (Postgres, SQLite, or MySQL URL), `sqlite_safety_check`
does not raise even when `master_data.db` is present.

**Pass criteria:** no exception for Postgres, SQLite, and MySQL explicit URLs.

**Skip condition:** `src.master.db.sqlite_safety_check` not available.

---

### TC-27: Global staff (NULL scope_id) lookup and update

**Phase:** M1 (addresses M-3 carried item)
**Automated:** automated
**File:** `tests/master/db/test_m1_carried_items.py::TestGlobalStaffNullScopeId`

`staffs.py` helpers `_update_staff`, `_set_default_staff`, and `_delete_staff`
use `scope_id IS :scope_id` which is invalid on Postgres.  These tests verify
that global-scope staff operations (where `scope_id=NULL`) work correctly.

Sub-cases:
- `_update_staff` finds and updates a global staff with `scope_id=None`
- `_update_staff` returns 404 when `scope_id=None` is used to search workspace-scope staff
- `_set_default_staff` marks a global staff as default
- `_delete_staff` removes a global staff row
- `_delete_staff` returns 404 when the global staff does not exist

Cross-dialect: Postgres variant skips until `TEST_POSTGRES_URL` is set.

**Pass criteria (SQLite):** all operations succeed on SQLite (M0 leniency).
**Pass criteria (Postgres):** all operations succeed on Postgres (fails until M-3 fix).

---

### TC-28: Global staff cross-scope isolation

**Phase:** M1 (part of M-3 regression coverage)
**Automated:** automated (bundled with TC-27)

Searching for a workspace-scope staff using `scope_id=None` must NOT find it
(the NULL comparison must be exact, not accidental IS-match-all).

---

### TC-29: `schema_info` returns correct columns

**Phase:** M1 (addresses m-2 carried item)
**Automated:** automated
**File:** `tests/master/db/test_m1_carried_items.py::TestSchemaInfoViaInspect`

`schema_info(db_path)` must return the correct set of column names for all tables
via `sqlalchemy.inspect()`, not `PRAGMA table_info`. The test includes a lint-level
check that the source no longer contains `"PRAGMA"`.

**Pass criteria:** correct columns returned; no PRAGMA in source after fix.

---

### TC-30: `schema_info` works on Postgres

**Phase:** M1 (m-2 carried item, CI matrix leg)
**Automated:** automated (skipped when `TEST_POSTGRES_URL` unset)
**File:** `tests/master/db/test_m1_carried_items.py::TestSchemaInfoViaInspect::test_schema_info_works_on_postgres`

`schema_info` returns non-empty column lists for all tables on Postgres.
Fails in M0 (PRAGMA fails on Postgres); passes after m-2 fix.

---

### M1 UAT

UAT runs against the staging singleton on bundled Postgres.
All `automated` cases can be verified by API calls, CLI invocations, and log assertions.
`needs-human` cases require real Slack/Discord interaction or visual UI verification.

#### UAT-M1-01: Fresh staging deploy — empty DB auto-migrated

**Automated:** automated

On first deploy (clean Postgres volume), master starts without operator intervention.
Verify via the `/health` endpoint (HTTP 200) and `alembic_version` row in the DB
(via `scripts/db.sh verify-schema` exit 0).

**Pass criteria:** HTTP 200 on `/health`; `scripts/db.sh verify-schema` exits 0.

---

#### UAT-M1-02: `snapshot-and-migrate` from SQLite — row counts, checksums, app works

**Automated:** automated (row counts + checksums); **needs-human** (app verification)

Before the staging Postgres deploy, run:
```
scripts/db.sh snapshot-and-migrate \
    --source-url "sqlite+pysqlite:////path/to/master_data.db" \
    --target-url "$POSTGRES_URL"
```

Automated assertions:
- `scripts/db.sh verify-schema` exits 0 after migration
- Row counts in Postgres match `manifest.json` for all 8 data areas: workspaces,
  topics, messages/chunks, staffs (global/workspace/topic), config/env, event_actions,
  notes/attachments, tasks/staged_dispatches
- SHA-256s in manifest match actual JSONL files

Human verification:
- Existing workspaces and topics visible in the UI
- Message history loads for at least one topic
- Staff list shows correct staff for a workspace
- Config overrides are active

---

#### UAT-M1-03: Safety net — unset DATABASE_URL with existing SQLite refuses

**Automated:** automated

Deploy a build where `DATABASE_URL` is **not** set in `.env.staging` and a
`master_data.db` file exists in `MASTER_DATA_DIR`. Verify:
- master exits immediately (non-zero)
- Container logs contain `"master_data.db"` and `"(a)"` and `"(b)"`

**Pass criteria:** master does not start; expected substrings in logs.

---

#### UAT-M1-04: Schema-behind refusal and explicit `scripts/db.sh migrate` recovery

**Automated:** automated

Manually set `alembic_version` to a fake old revision in the staging Postgres DB,
then attempt to restart master.  Verify:
- master exits non-zero (container exits immediately)
- logs contain `"behind"` and `"scripts/db.sh migrate"`

Recovery: run `scripts/db.sh migrate` against staging Postgres → master restarts
normally, `verify-schema` exits 0.

**Pass criteria:** refusal on bad version; recovery via `migrate` works.

---

#### UAT-M1-05: Restart persistence — data survives Postgres volume restart

**Automated:** automated

Create a workspace and topic via the API, restart the master container (not the
Postgres container), then verify the workspace and topic are still visible.

**Pass criteria:** workspace and topic survive container restart.

---

#### UAT-M1-06: Managed-DB mode (`COMPOSE_PROFILES` unset + external `DATABASE_URL`)

**Automated:** needs-human

Remove `COMPOSE_PROFILES=db-bundled` from `.env.staging` and set `DATABASE_URL`
to an external Postgres URL. Verify master connects, migrates (or starts normally),
and the bundled Postgres service is NOT running.

**Pass criteria:** master connects to external Postgres; bundled service absent from
`docker ps` output.

---

#### UAT-M1-07: Rollback to SQLite by resetting `DATABASE_URL`

**Automated:** needs-human

Set `DATABASE_URL=sqlite+pysqlite:///...` in the environment, restart master.
Verify master starts on SQLite and serves the original SQLite data.  Confirm
Postgres data is intact (the migration tool never writes to the source).

**Pass criteria:** master starts on SQLite; Postgres data still present.

---

#### UAT-M1-08: M0 regression smoke on Postgres — 8-area checklist

**Automated:** needs-human

Re-run the M0 staging smoke test against the Postgres-backed staging singleton.
Cover all 8 areas:

1. **Startup / existing data** — master starts with Postgres data; all workspaces visible.
2. **Messaging / streaming** — send a message in a topic; chunks stream to the UI.
3. **Concurrent topics** — open two topics simultaneously; both receive responses.
4. **Staff CRUD** — create/update/delete a staff at global, workspace, and topic scope.
5. **Config / env overrides** — set a config key at workspace scope; verify it overrides global.
6. **Event actions / scheduler** — create a `topic_message_sent` event action; trigger it.
7. **Notes / attachments / export / archive / notifications / delegation** — create a note; upload an attachment; export a topic; archive a workspace; verify at least one notification channel fires.
8. **Restart persistence** — restart master; verify all the above data is still present.

**Pass criteria:** human tester marks each area ✅ in the PR comment.

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
| `src.master.db.startup_db(url: str)` | Empty DB → auto-upgrade; pre-baseline → stamp; behind → SystemExit(1) with §5 message; ahead → SystemExit(1); up-to-date → no-op | M1 |
| `src.master.db.sqlite_safety_check(data_dir: str)` | No-op when DATABASE_URL set; SystemExit(1) with path + (a)/(b) remediation when SQLite file found and DATABASE_URL unset; no-op when no file | M1 |
| `src.master.db.resolve_db_url()` | Default URL is `postgresql+psycopg://...` when DATABASE_URL and MASTER_DATA_DIR unset | M1 |
| `src.master.db.schema_info(db_path)` | Returns columns via `sqlalchemy.inspect()`; source contains no `PRAGMA` | M1 |
| `staffs.py::_update_staff(conn, body, scope_type, None, name)` | Finds global staff (scope_id=NULL) correctly on all dialects | M1 |
| `staffs.py::_delete_staff(conn, scope_type, None, name)` | Deletes global staff (scope_id=NULL) correctly on all dialects | M1 |
| `staffs.py::_set_default_staff(conn, scope_type, None, name)` | Sets global default staff correctly on all dialects | M1 |
| `scripts/db.sh snapshot export --source-url URL --out DIR` | Exit 0; creates manifest.json + per-table JSONL; manifest SHA-256s valid; booleans as JSON bool; NULLs as JSON null; JSON columns embedded | M1 |
| `scripts/db.sh snapshot import --in DIR --target-url URL [--yes]` | Exit 0 on success; refuses non-empty without --yes; refuses schema mismatch; refuses tampered checksum; resets sequences | M1 |
| `scripts/db.sh snapshot-and-migrate` | Fully implemented (not stubbed); exit 0 on success | M1 |
