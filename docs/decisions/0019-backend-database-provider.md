---
title: "ADR-0019: Backend database provider — Postgres default, SQLite and MySQL supported, via SQLAlchemy Core + Alembic"
status: accepted
date: 2026-10-24
decision-makers: [architect, project-owner]
consulted: [engineer, tester, sre]
informed: [doc-writer, users]
---

## Context and Problem Statement

The master service persists all durable state in a single SQLite file
(`master_data.db`, ~670 lines of hand-rolled schema + migrations in
`src/master/db.py`, with raw `sqlite3` calls scattered across 15 other
production modules and 13 test modules). SQLite was chosen in ADR-0005
§4 for "zero infra overhead" with an explicit note that "migration to
PostgreSQL is deferred but will require a schema migration later."
GitHub issue
[#268](https://github.com/pandazxx/codex-slack/issues/268) now asks us
to honour that deferred migration: let operators pick the backend
database provider, make PostgreSQL the first-class choice, support
MySQL as an alternative, and ship data-migration tooling (snapshot +
import) so an existing SQLite install can move onto Postgres without
downtime beyond a stop-export-import-start window.

The code we have today is tightly bound to SQLite dialect features:
`INSERT OR IGNORE` / `INSERT OR REPLACE`, `ON CONFLICT (...) DO UPDATE
SET = excluded.X` (which Postgres also supports but MySQL spells
differently), `unixepoch('now','subsec')`, `PRAGMA foreign_keys` /
`PRAGMA table_info`, `sqlite_master` introspection, partial indexes
(`WHERE ... IS NULL`), expression indexes (`COALESCE(scope_id, '')`),
`INTEGER PRIMARY KEY AUTOINCREMENT` on `chunks.id`, table-recreation
migrations for CHECK-constraint changes, and ad-hoc
`sqlite3.OperationalError` swallowing in `init_db`. All call sites use
DB-API qmark `?` placeholders (SQLite's paramstyle), which do not work
in `text()` nor through psycopg (`%s`) or PyMySQL (`%s`). There are
also two threading surfaces: FastAPI request handlers (short-lived
connections per handler) and the background event worker (long-lived
connections). We need an abstraction that is honest about those
features without boiling the ocean.

## Decision Drivers

- *Postgres is the primary target.* It is the `DATABASE_URL` default,
  the dev-stack database, the staging singleton database, and the DB
  under UAT. SQLite and MySQL are supported alternatives, not the
  path we steer new users toward.
- *SQLite remains supported.* Existing single-user installs,
  contributors who want a zero-infra local setup, and the fast unit
  test loop keep working on SQLite. SQLite stays in the CI matrix.
- *MySQL remains supported.* Operators who already run MySQL can
  point `DATABASE_URL` at it. MySQL stays in the CI matrix. MySQL
  does not get UAT coverage — only CI unit tests.
- *Minimise call-site churn.* Sixteen production modules and thirteen
  test modules touch SQL directly today; rewriting them all onto an
  ORM is a multi-sprint rewrite we do not want to bundle with this
  change.
- *First-class migrations.* The in-code `_MIGRATIONS` list
  (28 ALTER-TABLE entries) and four table-recreation helpers already
  behave like a worse version of Alembic. CHECK-constraint evolution
  is going to keep happening (ADR-0013, ADR-0014 added event types;
  ADR-0017 added task states).
- *Operability.* One connection pool, one place to configure, one way
  to run migrations in CI and in deploy. The runbook should fit on
  one page.
- *Snapshot portability.* The migration-from-SQLite path has to work
  for real installs — not just a greenfield cutover — so we need a
  provider-neutral snapshot format and a round-trip import tool.
- *Test matrix cost.* Running the full stack suite against three
  dialects adds CI time. Keep the dialect-adaptation layer thin so
  the matrix stays per-PR rather than nightly.
- *Threat model unchanged.* Single-user, self-hosted (ADR-0009,
  ADR-0010). Encryption at rest is still out of scope. Plaintext DB
  connections are acceptable; TLS is not required by this ADR.

## Considered Options

1. **SQLAlchemy Core + Alembic** — one engine/pool, `text()` with named
   `:param` bindings for raw SQL where it already works cross-dialect,
   Table/expression API only where dialect divergence forces our
   hand. Alembic for migrations.
2. **Thin DIY dialect-abstraction layer** — keep raw SQL, add a tiny
   provider object (placeholder style, upsert syntax, "now()", etc.),
   hand-port the schema per provider, hand-write migrations per
   provider.
3. **SQLAlchemy ORM + Alembic (full ORM rewrite)** — model classes,
   Session, relationships, async where it fits. Largest rewrite,
   largest upside long-term.
4. **Lighter async ORM (`databases`, Tortoise, Peewee)** — smaller API
   surface than SQLAlchemy; most are async-first or sync-only,
   matching master's mixed-mode code awkwardly.

## Decision Outcome

*Chosen option:* **Option 1 — SQLAlchemy Core + Alembic** — because
it is the only option that gives us (a) a single, battle-tested pool
and dialect layer across all three providers, (b) a real migration
tool that replaces the hand-rolled `_MIGRATIONS` list and the four
table-recreation helpers in `src/master/db.py`, and (c) a gradual
porting path: we can keep raw SQL in `conn.execute(text("..."))` form
for queries that are already dialect-neutral, and only switch to the
Core expression API for the handful of statements that use
SQLite-only syntax. No module needs to be rewritten end-to-end on
day one.

**Postgres is the default.** `DATABASE_URL`'s default resolves to the
bundled Postgres service
(`postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master`);
the dev stack starts a Postgres container with a persisted volume;
staging and UAT run on Postgres. SQLite and MySQL remain fully
supported for operators who explicitly set `DATABASE_URL` to a
SQLite file or a MySQL instance, and both are exercised by CI on
every PR. Existing SQLite installs get a documented migration path
and a startup safety-net that refuses to launch on an empty Postgres
if an unassigned SQLite file is still on disk (see the design doc,
§5 and §9).

**Paramstyle choice.** Call sites move from DB-API qmark `?` with
positional tuples to SQLAlchemy `text()` with named `:param` bindings
and dict parameters. Phase 0 does this conversion mechanically. We
rejected `exec_driver_sql()` (which would keep `?` on SQLite and need
runtime rewriting to `%s` on psycopg/PyMySQL) because it bypasses
SQLAlchemy's type coercion, which is load-bearing on Postgres (bools
need to be real booleans, timestamps need to be text/timestamptz
depending on column, integer PKs need to round-trip through the
server-side sequence). Named `:param` is also easier to read where a
call site has 10+ parameters — the heaviest site today
(`orchestrate_api.py`) has 38.

**MySQL floor: 8.0.16+** — functional indexes land in 8.0.13; enforced
CHECK constraints land in 8.0.16. We refuse to start on anything
older. Documented, not silently degraded.

### Consequences

- *Good:* One connection abstraction, one migration tool, one place to
  configure the backend. Deletes ~250 lines of hand-rolled
  table-rebuild migrations in `db.py` over phase 1.
- *Good:* Alembic autogenerate makes future schema evolution auditable
  in diffs rather than archaeology through the `_MIGRATIONS` list.
- *Good:* SQLAlchemy's `Pool` solves the "every handler opens its own
  `sqlite3.connect()`" cost once we move to a server-based DB —
  otherwise Postgres would pay a full handshake per request.
- *Good:* Dev parity with staging/prod (both on Postgres with a
  persisted volume) catches dialect bugs during development, not in
  CI or UAT.
- *Bad:* Phase 0 is **not** a no-op. Converting qmark `?` to named
  `:param` is a mechanical but non-trivial diff across 16 production
  modules and 13 test modules. "No behaviour change" means "same
  results, same semantics" — not "same source text."
- *Bad:* Dependency weight grows (SQLAlchemy + Alembic +
  `psycopg[binary]` + `pymysql`). Measured on the slim base image, we
  expect roughly +15 MB installed, which is acceptable against the
  current image.
- *Bad:* Call sites keep raw `text()` SQL in phase 1, so we have *two*
  idioms (Core expressions where needed, text() everywhere else)
  until a future cleanup. We accept this because the alternative
  (one-shot ORM rewrite) is a worse bet.
- *Bad:* Partial indexes and expression indexes used today require
  MySQL 8.0.16+. Older MySQL will not be supported — documented, not
  silently degraded.
- *Bad:* Existing SQLite installs that upgrade without setting
  `DATABASE_URL` will now **refuse to start** rather than silently
  launching on an empty Postgres. This is intentional (we never want
  to leave user data in a stale SQLite file while master runs
  against an empty Postgres), but it requires an operator action on
  upgrade. The release notes and the migration runbook spell it out.

### Confirmation

- CI matrix runs the test suite against SQLite, Postgres, and MySQL
  on **every PR** (`.github/workflows/test.yml`). Postgres is top
  priority: Postgres failures block merge; SQLite and MySQL failures
  also block merge; MySQL does not gate UAT.
- A new `scripts/db_snapshot_roundtrip.sh` CI job exports a seeded
  SQLite DB, imports into Postgres, inserts into each
  identity/sequence table (e.g. `chunks`) to prove sequence reset,
  and runs a read-side smoke test. Fails the pipeline on row-count
  mismatch, schema drift, or sequence-conflict error.
- `docs/guides/runbooks/migrate-sqlite-to-postgres.md` ships
  alongside the phase 1 release and is dry-run-executed before
  merging.
- `scripts/db.sh --help` lists the subcommands `snapshot export`,
  `snapshot import`, `migrate`, `snapshot-and-migrate`, and
  `verify-schema`. The script is the single entry point; operators
  do not call Python modules directly.
- `src/master/db.py` becomes `src/master/db/` with a thin re-export
  of the SQLAlchemy engine/session factory; raw `sqlite3` imports
  outside the pre-baseline fixup module, the snapshot CLI, and the
  test suite are a lint-time error.
- On startup with no `DATABASE_URL` set and an existing
  `${MASTER_DATA_DIR}/master_data.db` on disk, master refuses to
  launch and emits a clear error message pointing at the migration
  runbook.

## Pros and Cons of the Options

### Option 1: SQLAlchemy Core + Alembic

One engine and connection pool, dialect-aware SQL compilation,
Alembic owns schema evolution. Call sites that are already
cross-dialect stay as `text("...")` strings with named `:param`
bindings; call sites that depend on SQLite dialect (`INSERT OR
IGNORE`, `unixepoch`, PRAGMAs, table rebuilds for CHECK changes)
move to the Core expression API or dialect-dispatched helpers.

- *Pro:* De-facto standard for Python + SQL. All three target
  dialects (SQLite, Postgres, MySQL) are first-party backends with
  active maintenance.
- *Pro:* Alembic handles exactly the schema patterns we're already
  struggling with (CHECK rebuilds, add-column migrations, data
  backfills).
- *Pro:* Core, not ORM — the mental model stays "write SQL, run
  SQL." Teams can read it without learning a mapping layer.
- *Pro:* Connection pool is a hard requirement once we leave SQLite.
  SQLAlchemy gives us one for free, tunable per environment.
- *Pro:* `text()` with named `:param` compiles to the right driver
  paramstyle automatically. We do not have to maintain a paramstyle
  translator.
- *Con:* Two co-existing idioms (`text()` strings vs. Core
  expressions) for the length of the phase-1 rollout. Mitigated by
  a lint rule banning `import sqlite3` outside allowlisted files.
- *Con:* Alembic autogenerate is imperfect — CHECK constraints,
  partial indexes, functional indexes, and identity/sequence resets
  need hand-written revisions. We're already hand-writing them, so
  no regression, but the "autogenerate will do it" story has an
  asterisk.

### Option 2: Thin DIY dialect-abstraction layer

A tiny provider object exposing `connection()`, `placeholder`
(`?`/`%s`), `upsert_sql()`, `now_sql()`, `partial_index_sql()`, and
`autoincrement_sql()`. Keep every raw SQL string in place; hand-port
the schema file per provider; hand-write migrations per provider.

- *Pro:* Zero new ORM concepts for the team.
- *Pro:* Smallest diff on day one — most SQL stays byte-identical.
- *Con:* We re-invent half of SQLAlchemy's dialect module, badly.
  Placeholder style is only the surface; parameter binding, type
  coercion (SQLite's dynamic typing vs. Postgres's strict typing),
  bytes vs. text, identity/sequence handling, and transaction
  semantics all leak out eventually.
- *Con:* Three parallel schema files drift silently. The notes table
  that landed in ADR-0015 was *one* additive change; drift risk
  compounds.
- *Con:* Migrations are now hand-written per provider. We stay in
  the world where "did SQLite 3.42 add this feature?" is a
  per-commit question.
- *Con:* No connection pool, no `text()` dialect compilation; everyone
  reinvents them locally. Already visible in `mqtt_client.py`'s
  per-call `sqlite3.connect()` pattern (12 sites).

### Option 3: SQLAlchemy ORM + Alembic (full rewrite)

Model classes with relationships; `Session` object; typed accessors;
Alembic autogenerate from models.

- *Pro:* Long-term, this is where a healthy codebase ends up. Query
  surface is typed; relationships are explicit; adding a column is
  one file.
- *Pro:* Autogenerate becomes more useful when models are the source
  of truth.
- *Con:* Full rewrite of sixteen production modules. Scope-blows this
  issue by ~5x.
- *Con:* Mixing async FastAPI handlers, sync background workers, and
  async SQLAlchemy requires discipline that today's code doesn't
  demonstrate. Risk of leaking sessions across threads/loops is
  non-trivial.
- *Con:* The orchestration module explicitly uses
  `threading.Lock` + `asyncio.to_thread` to bridge sync DB code into
  async; retro-fitting async DB there is a design change
  masquerading as a port.

### Option 4: Lighter async ORM (`databases`, Tortoise, Peewee)

Pick an alternative Python SQL library.

- *Pro:* Smaller surface than SQLAlchemy; some have nicer async
  stories.
- *Con:* Smaller communities, less mature multi-dialect support.
  Tortoise's MySQL + Postgres parity is uneven; `databases` is in
  near-maintenance mode. Peewee is sync-only.
- *Con:* Migrations stories are weaker than Alembic's across the
  board.
- *Con:* We buy a smaller abstraction at the cost of a worse
  ecosystem. Not worth it for a change we expect to live for years.
