---
title: "ADR-0020: MySQL support details — collation policy, large-text sizing, sql_mode enforcement, and the Phase 2 schema-change strategy"
status: proposed
date: 2026-10-31
decision-makers: [architect, project-owner]
consulted: [engineer, tester, senior-sre]
informed: [doc-writer]
---

## Context and Problem Statement

ADR-0019 chose SQLAlchemy Core + Alembic and committed to supporting
MySQL as a CI-tested alternative (no UAT). M1 landed Postgres as the
default; the MySQL CI leg was disabled by rev 4 of the design because the
baseline revision does not actually create the schema on MySQL. The
first CI attempt against `mysql:8.0` fails on table creation with:

```
(1101, "BLOB, TEXT, GEOMETRY or JSON column 'repo_ref' can't have a default value")
```

and once that is fixed, a short tail of MySQL-specific gaps follows:
TEXT columns in primary keys / unique keys / indexes (MySQL requires an
explicit length), four partial indexes that MySQL does not support, an
expression-based unique index, two tables with the reserved word `key`,
default collation that is case- and accent-insensitive, 64 KB TEXT size
limit for message bodies, and DDL auto-commit interactions with the
snapshot importer's single-transaction load.

Most of these have clean mechanical fixes (widths, generated columns,
`sa.Boolean`, `_dialect.upsert`, `_dialect.json_array_contains_fragment`,
etc.). Two decisions, though, are architecturally significant and need
to be settled before Phase 2 lands any code:

1. **Collation.** MySQL's default `utf8mb4_0900_ai_ci` is case- and
   accent-insensitive, which changes the behaviour of unique indexes
   (`workspaces.name`, `staffs.name`, `notes.key`), `=` comparisons,
   and `ORDER BY` relative to SQLite and Postgres. Any choice we make
   here propagates into semantics the product owner has to agree to.
2. **Schema-change strategy for an already-released baseline.** The
   existing baseline revision (`000000000001`) is in the hands of
   every Phase 1 install (SQLite with legacy pre-baseline stamp, and
   Postgres with Alembic autogenerate). MySQL support requires schema
   changes that touch Postgres and SQLite DDL as well (Text →
   String(n), dropped `server_default` on `workspaces.repo_ref`,
   generated columns for the partial-index conversions). We have to
   decide whether those changes are a new revision or an in-place
   edit of the baseline, and we have to be able to prove, in CI,
   that we did not break existing Postgres/SQLite installs.

The ADR records the policy for both. The gap list and migration
mechanics live in the design doc §13.

## Decision Drivers

- *Parity with SQLite and Postgres on equality semantics.* The product
  treats `workspace.name` and `staff.name` as case-sensitive today
  (SQLite BINARY by default, Postgres default collation
  `pg_catalog."C"` on the schema in use). MySQL silently flipping that
  to case-insensitive is a semantic change for every operator who
  stores two names differing only in case.
- *Failure visibility.* If a MySQL instance has non-matching
  collation/`sql_mode` set at the server level, we want master to
  **refuse to start** with an actionable message, not misbehave at
  runtime. This matches ADR-0019's "documented, not silently degraded"
  posture for the 8.0.16 version floor. (The 8.0.16 floor is the
  *minimum* supported server for managed/external MySQL; CI tests
  `mysql:8.4` only — see the Addendum.)
- *Minimal migration surface for existing installs.* Phase 1 is live
  on at least one Postgres staging host and the SQLite legacy fleet.
  Any schema change we ship must be a forward migration those installs
  can apply with `scripts/db.sh migrate` without a snapshot cycle.
- *No silent DDL churn in CI.* The baseline is the shape every fresh
  install starts from. Changing it in place would make Alembic's
  "baseline is accepted history" guarantee a lie. Changing it in place
  silently (no diff review possible) would be worse.
- *CI must prove parity.* If we claim SQLite and Postgres DDL are
  unchanged after the Phase 2 schema work, CI must enforce it.

## Considered Options

### Collation

- *C1.* Require `utf8mb4_bin` server-wide. Byte-exact equality
  matches SQLite's BINARY. Startup checks `@@collation_server` and
  `@@collation_database`.
- *C2.* Require `utf8mb4_0900_as_cs` server-wide. Case- and
  accent-sensitive, Unicode-aware ordering (closer to Postgres
  default than `utf8mb4_bin`'s raw byte order).
- *C3.* Set collation per column in the baseline (`mysql_collation=
  'utf8mb4_0900_as_cs'` on each String(n) column). No server-level
  requirement.
- *C4.* Accept the MySQL default `utf8mb4_0900_ai_ci` and document the
  behavioural difference.

### Baseline-change strategy

- *S1.* **Edit the baseline revision in place.** Risk: existing
  Postgres installs stamped at `000000000001` will not re-run the
  baseline, so any diff would silently diverge from the live schema.
- *S2.* **New revision `000000000002_mysql_dialect_fixes.py`** that
  carries every schema change needed for MySQL (Text → String(n),
  dropped `server_default` on `repo_ref`, generated columns + indexes
  replacing the partial indexes, UNIQUE constraints replacing the
  expression unique index). Postgres and SQLite installs re-apply it
  as a forward migration. The baseline stays untouched.
- *S3.* **Hybrid:** keep the baseline for Postgres/SQLite as-is, add a
  parallel MySQL-only baseline (`001_baseline_mysql.py`) branched on
  dialect. Two schemas to keep in sync forever.

## Decision Outcome

### Collation — *Chosen option: C2* — require `utf8mb4_0900_as_cs` server-wide, verified at startup.

- Byte-exact (`utf8mb4_bin`) would preserve SQLite's BINARY equality
  but gives linguistically wrong `ORDER BY` on multilingual names
  (`é` sorts after `z`). `utf8mb4_0900_as_cs` keeps case- and
  accent-sensitive equality — the semantic property we need — while
  giving a Unicode-aware sort, closer to Postgres's default behaviour.
- Server-wide, not per-column, so `CREATE TABLE … LIKE` and ad-hoc
  queries from the mysql client inherit the same semantics as the
  app. Reduces the "did I remember to set it on this column" risk.
- Startup check queries `@@collation_server` and
  `@@collation_database`; master refuses to start if either is not
  `utf8mb4_0900_as_cs` and prints the compose-flag fix. The CI MySQL
  service and `docker-compose.mysql.yml` set this collation via the
  server `--collation-server` flag and `CREATE DATABASE … COLLATE`.

**Owner sign-off: needed.** Changing from the MySQL default has a
visible impact on operators pointing `DATABASE_URL` at a managed
MySQL that uses `_ai_ci`: they will have to `ALTER DATABASE master
COLLATE utf8mb4_0900_as_cs` or master will refuse to start. The
runbook spells it out, but this is a hard refusal, not a soft warning.

### Baseline-change strategy — *Chosen option: S2* — new revision, baseline stays byte-identical.

- The baseline revision is cryptographic in effect: every Postgres
  install stamped at `000000000001` will not re-apply it. Editing it
  silently is unsafe. Editing it in place "only for MySQL" is a
  slippery slope since the actual gap fixes (Text → String(n),
  dropped `server_default`, generated columns) are **not** MySQL-only
  DDL — they emit different `CREATE TABLE` output on Postgres and
  SQLite too.
- A new revision `000000000002_mysql_dialect_fixes.py` is a forward
  migration: Postgres installs run `ALTER TABLE … ALTER COLUMN …
  TYPE VARCHAR(n)` (noop in practice because Postgres TEXT and
  VARCHAR share storage), SQLite rewrites TEXT → VARCHAR(n) (noop in
  practice because SQLite's affinity treats them the same), and
  fresh MySQL installs get the right shape from the start by
  applying both revisions in sequence.
- The exceptions-that-prove-the-rule are MySQL-only Alembic kwargs
  that **do not** change SQLite/PG DDL. Those are rare enough that
  we never edit the baseline for them; add them to the new revision
  too, scoped by `dialect_name == 'mysql'` where needed, so the
  baseline stays frozen.

The rule, in one sentence: **the baseline revision is frozen; every
schema change goes into a new revision, including MySQL-only
tweaks.**

**Owner sign-off: needed.** The alternative (S1 edit-in-place) is
tempting because it keeps the migration history short, but it would
force every live Postgres install to rebuild its `alembic_version`
story from scratch and we lose the ability to reason about what a
"baseline install" actually contains.

### Confirmation

- CI gate `test-mysql` is re-enabled and added to the `Build and test`
  needs list. Postgres, SQLite and MySQL all block merge.
- A new CI job `baseline-ddl-stability` compiles the baseline revision
  against SQLite and PostgreSQL dialects, canonicalises the DDL
  (whitespace-normalised), and diffs against a committed fixture
  under `tests/master/db/fixtures/baseline_ddl_{sqlite,postgresql}.sql`.
  Any schema change that is not scoped to a new revision fails this
  gate. The job runs on every PR.
- A new CI job inside the mysql leg asserts the server collation
  (`SELECT @@collation_server`) is `utf8mb4_0900_as_cs` and that
  `startup_db` succeeds against the configured URL.
- `scripts/db.sh verify-schema` grows a `--check-collation` mode that
  can be run against staging/prod to confirm parity.
- A SQLite → MySQL snapshot round-trip CI job is added, mirroring the
  SQLite → Postgres job that landed in M1.

## Pros and Cons of the Options

### C1 — `utf8mb4_bin` server-wide

- Pro: Byte-exact; matches SQLite BINARY equality.
- Pro: Fastest comparison (no collation table lookup).
- Con: ORDER BY sorts by raw byte, which is wrong for most
  non-ASCII names.
- Con: Mixed-case `foo` and `FOO` are different, but so are `é` and
  `é` normalised differently — unexpected for users typing names.

### C2 — `utf8mb4_0900_as_cs` server-wide (chosen)

- Pro: Case- and accent-sensitive equality matches the semantics
  Postgres and SQLite already give today.
- Pro: Unicode-aware sort, consistent with Postgres default on most
  operator setups.
- Con: Non-default server flag that managed-MySQL operators must
  explicitly configure or master refuses to start.

### C3 — Per-column collation

- Pro: Does not change server defaults; schema file is self-contained.
- Con: Easy to miss a column when adding new ones; every `ALTER TABLE`
  that forgets `COLLATE` reverts to server default.
- Con: Ad-hoc queries from the mysql CLI silently use the server
  default; developers get different behaviour from the app.

### C4 — Accept the MySQL default

- Pro: Zero friction for managed-MySQL operators.
- Con: `workspaces.name` and `staffs.name` lose case/accent sensitivity,
  which is a user-visible behaviour change relative to Postgres and
  SQLite installs. ADR-0019 is explicit that MySQL is a supported
  alternative, not a degraded one — this would make it degraded.

### S1 — Edit the baseline in place

- Pro: Shorter migration history; "the baseline" is the current shape.
- Con: Existing Postgres installs stamped at `000000000001` never
  re-apply the baseline, so an in-place edit silently diverges from
  their live schema. There is no mechanism to detect the drift.
- Con: Makes "what does `000000000001` contain" ambiguous — the file
  changes but the stamp does not.

### S2 — New revision (chosen)

- Pro: Forward migration applies cleanly to Postgres and SQLite
  installs. The baseline is a frozen historical artefact.
- Pro: Diffable: the new revision IS the diff. Review is
  straightforward.
- Con: Longer revision chain. Minor; this is Alembic's normal mode of
  operation.

### S3 — Hybrid dialect-branched baseline

- Pro: MySQL baseline can be hand-tuned without touching Postgres.
- Con: Two schemas to keep in sync forever. Any ADR-worthy schema
  change doubles its diff surface. Rejected.

## Addendum (2026-10-31) — MySQL image pin and `caching_sha2_password`

The design-doc §13 D6 originally recommended pinning `mysql:8.0`, with
the stated rationale that pymysql "still relies on `mysql_native_password`
for some configurations". That rationale is incorrect and the pin is
no longer safe:

- **MySQL 8.0 reached end-of-life in April 2026.** Pinning `mysql:8.0`
  means shipping on an unsupported server.
- **8.4 (LTS) disables `mysql_native_password` by default** and uses
  `caching_sha2_password` instead.
- **PyMySQL has supported `caching_sha2_password` since 0.9.3.** The
  only subtlety is that the first full authentication for a given
  user over a non-TLS connection needs the RSA public-key exchange,
  which requires the `cryptography` Python package. Fast-auth after
  the server has cached the credentials does not.

Decision (recorded here for the architectural scope of this ADR; the
mechanical fix is in design-doc §13 D6, OQ16, the risk note, and the
compose/CI snippets):

- **Pin `mysql:8.4` in `docker-compose.mysql.yml` and `docker-compose.ci.yml`.**
  Explicitly `mysql:8.4`, not `mysql:8` or `latest`, so a tag change
  cannot silently swap us to another major.
- **Add `cryptography` to `requirements.txt`** (or pin it directly if
  it is already a transitive dependency of our existing stack), so
  pymysql can complete first-time `caching_sha2_password`
  authentication over the plain TCP connection used by compose and CI.
- **New CI test:** create a fresh MySQL user on the 8.4 service and
  authenticate it over a non-TLS TCP connection. This exercises the
  RSA public-key exchange path and guards against `cryptography`
  disappearing silently from the dependency graph.
- **Do not set `--default-authentication-plugin` server flag.** It
  was **removed in 8.4** in favour of `authentication_policy`; the
  image default (`caching_sha2_password`) is already what we want.
  Any compose, CI, or runbook snippet that mentions the old flag
  must be updated.
- **The 8.0.16 version floor is preserved for managed/external
  MySQL** — that is the documented *minimum supported* server — but
  **CI tests `mysql:8.4` only**. 8.0.16–8.0.x is "best effort",
  unverified post-EOL. The startup version check still enforces
  `(major, minor, patch) >= (8, 0, 16)` so an operator on a legacy
  managed instance gets a clear refusal rather than a silent failure
  later.

Confirmation additions:

- CI job `mysql-fresh-user-auth` passes: a brand-new user authenticates
  against `mysql:8.4` over plain TCP.
- `requirements.txt` grep for `cryptography` matches (or the lock
  file resolves it to a pinned version).
- Compose and CI snippets contain no `--default-authentication-plugin`
  flag.

This Addendum does **not** change the Collation or Baseline-change
Strategy decisions above. Owner sign-off needed for the image-pin
change (OQ16 in the design doc).
