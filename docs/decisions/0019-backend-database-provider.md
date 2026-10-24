---
title: "ADR-0019: Backend database provider — pluggable Postgres/MySQL via SQLAlchemy Core + Alembic"
status: proposed
date: 2026-10-24
decision-makers: [architect, project-owner]
consulted: [engineer, tester, sre]
informed: [doc-writer, users]
---

## Context and Problem Statement

The master service persists all durable state in a single SQLite file
(`master_data.db`, ~670 lines of hand-rolled schema + migrations in
`src/master/db.py`, with raw `sqlite3` calls scattered across ten other
modules). SQLite was chosen in ADR-0005 §4 for "zero infra overhead" with
an explicit note that "migration to PostgreSQL is deferred but will
require a schema migration later." GitHub issue
[#268](https://github.com/pandazxx/codex-slack/issues/268) now asks us to
honour that deferred migration: allow operators to pick the backend
database provider, prioritising PostgreSQL, then MySQL, and ship data
migration tooling (snapshot + import) so an existing SQLite install can
move onto Postgres without downtime beyond a stop-export-import-start
window.

The code we have today is tightly bound to SQLite dialect features:
`INSERT OR IGNORE` / `INSERT OR REPLACE`, `ON CONFLICT (...) DO UPDATE SET
= excluded.X` (which Postgres also supports but MySQL spells differently),
`unixepoch('now','subsec')`, `PRAGMA foreign_keys` / `PRAGMA table_info`,
`sqlite_master` introspection, partial indexes (`WHERE ... IS NULL`),
expression indexes (`COALESCE(scope_id, '')`), table-recreation migrations
for CHECK-constraint changes, and ad-hoc `sqlite3.OperationalError`
swallowing in `init_db`. There are also two threading surfaces: FastAPI
request handlers (short-lived connections per handler) and the background
event worker (long-lived connections). We need an abstraction that is
honest about those features without boiling the ocean.

## Decision Drivers

- *Prioritise Postgres as a first-class target*, MySQL second, SQLite
  retained as the default for dev/test (per issue #268).
- *Minimise call-site churn.* Ten modules touch SQL directly today;
  rewriting them all onto an ORM is a multi-sprint rewrite we do not want
  to bundle with this change.
- *First-class migrations.* The in-code `_MIGRATIONS` list and
  table-recreation helpers already behave like a worse version of Alembic.
  CHECK-constraint evolution is going to keep happening (ADR-0013,
  ADR-0014 added new event types; ADR-0017 added task states).
- *Operability.* One connection pool, one place to configure, one way to
  run migrations in CI and in deploy. The runbook should fit on one page.
- *Snapshot portability.* The migration-from-SQLite path has to work for
  real installs — not just a greenfield cutover — so we need a
  provider-neutral snapshot format and a round-trip import tool.
- *Test matrix cost.* Running the full stack suite against Postgres (and
  later MySQL) adds CI time. The dialect-adaptation layer must be thin
  enough that most tests still run on SQLite with a small Postgres-only
  smoke suite catching the gaps.
- *Threat model unchanged.* Single-user, self-hosted (ADR-0009, ADR-0010).
  Encryption at rest is still out of scope; the new provider must not
  silently *weaken* the posture (e.g. plaintext secrets over the wire to
  a remote Postgres host).

## Considered Options

1. **SQLAlchemy Core + Alembic** — one engine/pool, `text()` for raw SQL
   where it already works cross-dialect, Table/expression API only where
   dialect divergence forces our hand. Alembic for migrations.
2. **Thin DIY dialect-abstraction layer** — keep raw SQL, add a tiny
   provider object (placeholder style, upsert syntax, "now()", etc.),
   hand-port the schema per provider, hand-write migrations per provider.
3. **SQLAlchemy ORM + Alembic (full ORM rewrite)** — model classes,
   Session, relationships, async where it fits. Largest rewrite, largest
   upside long-term.
4. **Lighter async ORM (`databases`, Tortoise, Peewee)** — smaller API
   surface than SQLAlchemy; most are async-first or sync-only, matching
   master's mixed-mode code awkwardly.

## Decision Outcome

*Chosen option:* **Option 1 — SQLAlchemy Core + Alembic** — because it is
the only option that gives us (a) a single, battle-tested pool and
dialect layer across all three providers, (b) a real migration tool that
replaces the hand-rolled `_MIGRATIONS` list and the four
table-recreation helpers in `src/master/db.py`, and (c) a gradual
porting path: we can keep raw SQL in `conn.execute(text("..."))` form for
queries that are already dialect-neutral, and only switch to the Core
expression API for the handful of statements that use SQLite-only syntax.
No module needs to be rewritten end-to-end on day one.

SQLite stays the default (`DATABASE_URL=sqlite+pysqlite:///{MASTER_DATA_DIR}/master_data.db`)
so existing installs, the dev stack, and the test suite all keep working
with no config change. Postgres becomes the recommended production
provider after phase 1 lands. MySQL support ships in phase 2, with a
documented minimum version (MySQL 8.0.13+) so we can rely on functional
indexes and enforced CHECK constraints.

### Consequences

- *Good:* One connection abstraction, one migration tool, one place to
  configure the backend. Deletes ~250 lines of hand-rolled table-rebuild
  migrations in `db.py` over phase 1.
- *Good:* Alembic autogenerate makes future schema evolution auditable in
  diffs rather than archaeology through the `_MIGRATIONS` list.
- *Good:* SQLAlchemy's `Pool` solves the "every handler opens its own
  `sqlite3.connect()`" cost once we move to a server-based DB — otherwise
  Postgres would pay a full handshake per request.
- *Bad:* Dependency weight grows (SQLAlchemy + Alembic + `psycopg[binary]`
  + `pymysql`/`mysqlclient`). Measured on the slim base image, we expect
  roughly +15 MB installed, which is acceptable against the current
  image.
- *Bad:* Call sites keep raw `text()` SQL in phase 1, so we have *two*
  idioms (Core expressions where needed, text() everywhere else) until a
  future cleanup. We accept this because the alternative (one-shot ORM
  rewrite) is a worse bet.
- *Bad:* Partial indexes and expression indexes used today require
  MySQL 8.0.13+. Older MySQL will not be supported — documented, not
  silently degraded.

### Confirmation

- CI matrix runs the test suite against both SQLite and Postgres after
  phase 1 (`.github/workflows/test.yml`). After phase 2, the matrix
  gains a MySQL leg (either per-PR or nightly — see Open Question 5).
- A new `scripts/db_snapshot_roundtrip.sh` CI job exports a seeded
  SQLite DB, imports into Postgres, and runs a read-side smoke test
  against the Postgres copy. Fails the pipeline on row-count mismatch
  or schema drift.
- `docs/guides/runbooks/migrate-sqlite-to-postgres.md` ships alongside
  the phase 1 release and is dry-run-executed before merging.
- `src/master/db.py` is reduced to a thin re-export of the SQLAlchemy
  engine/session factory; raw `sqlite3` imports outside tests and the
  snapshot CLI are a lint-time error.

## Pros and Cons of the Options

### Option 1: SQLAlchemy Core + Alembic

One engine and connection pool, dialect-aware SQL compilation, Alembic
owns schema evolution. Call sites that are already cross-dialect stay as
`text("...")` strings; call sites that depend on SQLite dialect
(`INSERT OR IGNORE`, `unixepoch`, PRAGMAs, table rebuilds for CHECK
changes) move to the Core expression API or dialect-dispatched helpers.

- *Pro:* De-facto standard for Python + SQL. All three target dialects
  (SQLite, Postgres, MySQL) are first-party backends with active
  maintenance.
- *Pro:* Alembic handles exactly the schema patterns we're already
  struggling with (CHECK rebuilds, add-column migrations, data
  backfills).
- *Pro:* Core, not ORM — the mental model stays "write SQL, run SQL."
  Teams can read it without learning a mapping layer.
- *Pro:* Connection pool is a hard requirement once we leave SQLite.
  SQLAlchemy gives us one for free, tunable per environment.
- *Con:* Two co-existing idioms (`text()` strings vs. Core expressions)
  for the length of the phase-1 rollout. Mitigated by a lint rule
  banning `import sqlite3` outside allowlisted files.
- *Con:* Alembic autogenerate is imperfect — CHECK constraints, partial
  indexes, and functional indexes need hand-written revisions. We're
  already hand-writing them, so no regression, but the "autogenerate
  will do it" story has an asterisk.

### Option 2: Thin DIY dialect-abstraction layer

A tiny provider object exposing `connection()`, `placeholder`
(`?`/`%s`), `upsert_sql()`, `now_sql()`, and `partial_index_sql()`.
Keep every raw SQL string in place; hand-port the schema file per
provider; hand-write migrations per provider.

- *Pro:* Zero new ORM concepts for the team.
- *Pro:* Smallest diff on day one — most SQL stays byte-identical.
- *Con:* We re-invent half of SQLAlchemy's dialect module, badly.
  Placeholder style is only the surface; parameter binding, type
  coercion (SQLite's dynamic typing vs. Postgres's strict typing),
  bytes vs. text, and transaction semantics all leak out eventually.
- *Con:* Three parallel schema files drift silently. The notes table
  that landed in ADR-0015 was *one* additive change; drift risk
  compounds.
- *Con:* Migrations are now hand-written per provider. We stay in the
  world where "did SQLite 3.42 add this feature?" is a per-commit
  question.
- *Con:* No connection pool, no `text()` dialect compilation; everyone
  reinvents them locally. Already visible in `mqtt_client.py`'s
  per-call `sqlite3.connect()` pattern.

### Option 3: SQLAlchemy ORM + Alembic (full rewrite)

Model classes with relationships; `Session` object; typed accessors;
Alembic autogenerate from models.

- *Pro:* Long-term, this is where a healthy codebase ends up. Query
  surface is typed; relationships are explicit; adding a column is one
  file.
- *Pro:* Autogenerate becomes more useful when models are the source of
  truth.
- *Con:* Full rewrite of ten modules. Scope-blows this issue by ~5x.
- *Con:* Mixing async FastAPI handlers, sync background workers, and
  async SQLAlchemy requires discipline that today's code doesn't
  demonstrate. Risk of leaking sessions across threads/loops is
  non-trivial.
- *Con:* The orchestration module explicitly uses
  `threading.Lock` + `asyncio.to_thread` to bridge sync DB code into
  async; retro-fitting async DB there is a design change masquerading
  as a port.

### Option 4: Lighter async ORM (`databases`, Tortoise, Peewee)

Pick an alternative Python SQL library.

- *Pro:* Smaller surface than SQLAlchemy; some have nicer async stories.
- *Con:* Smaller communities, less mature multi-dialect support.
  Tortoise's MySQL + Postgres parity is uneven; `databases` is in
  near-maintenance mode. Peewee is sync-only.
- *Con:* Migrations stories are weaker than Alembic's across the board.
- *Con:* We buy a smaller abstraction at the cost of a worse ecosystem.
  Not worth it for a change we expect to live for years.
