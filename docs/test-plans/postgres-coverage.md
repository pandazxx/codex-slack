# Test Plan: Postgres Coverage Gap

**Branch:** `test/postgres-coverage`
**Refs:** #268
**Date:** 2026-11-01

## Background

ADR-0019 established Postgres as the production default. Several tests bypassed
`DATABASE_URL` by calling `sqlite3.connect()` directly, making them silent on
the Postgres CI leg. This plan closes that gap.

## Route Inventory

All API routes listed below. "Covered" means at least one existing test calls
the endpoint through the TestClient (using the configured backend) and asserts
a response shape.

| Route | Status |
|-------|--------|
| GET  /api/workspaces | covered — test_main.py |
| POST /api/workspaces | covered — test_main.py |
| GET  /api/workspaces/{workspace_id} | covered — test_main.py |
| DELETE /api/workspaces/{workspace_id} | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/refresh-auth | covered — test_endpoint_coverage.py (added) |
| POST /api/workspaces/{workspace_id}/restart-agent | covered — test_endpoint_coverage.py (added) |
| GET  /api/workspaces/{workspace_id}/agent-status | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/branches | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/config | covered — test_main.py |
| PATCH /api/workspaces/{workspace_id}/config | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/topics | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/topics | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id} | covered — test_main.py |
| DELETE /api/workspaces/{workspace_id}/topics/{topic_id} | covered — test_main.py |
| PATCH /api/workspaces/{workspace_id}/topics/{topic_id}/current-staff | covered — test_endpoint_coverage.py (added) |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/messages | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/messages | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/messages/{message_id}/cancel | covered — test_endpoint_coverage.py (added) |
| GET  /api/staffs | covered — test_main.py |
| POST /api/staffs | covered — test_main.py |
| PUT  /api/staffs/{name} | covered — test_main.py |
| DELETE /api/staffs/{name} | covered — test_main.py |
| POST /api/staffs/{name}/set-default | covered — test_endpoint_coverage.py (added) |
| GET  /api/workspaces/{workspace_id}/staffs | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/staffs | covered — test_main.py |
| PUT  /api/workspaces/{workspace_id}/staffs/{name} | covered — test_main.py |
| DELETE /api/workspaces/{workspace_id}/staffs/{name} | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/staffs/{name}/set-default | covered — test_endpoint_coverage.py (added) |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/staffs | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/staffs | covered — test_endpoint_coverage.py (added via fixture) |
| PUT  /api/workspaces/{workspace_id}/topics/{topic_id}/staffs/{name} | covered — test_endpoint_coverage.py (added) |
| DELETE /api/workspaces/{workspace_id}/topics/{topic_id}/staffs/{name} | covered — test_endpoint_coverage.py (added) |
| GET  /api/config | covered — test_main.py |
| PATCH /api/config | covered — test_main.py |
| GET  /api/config/system-settings | covered — test_endpoint_coverage.py (added) |
| PATCH /api/config/system-settings | covered — test_endpoint_coverage.py (added) |
| GET  /api/config/system-variables | covered — test_endpoint_coverage.py (added) |
| GET  /api/topics/recent | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/export | covered — test_topic_export.py |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/attachments | covered — test_main.py |
| GET  /api/attachments/{attachment_id}/download | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/event-actions | covered — test_endpoint_coverage.py (added) |
| POST /api/workspaces/{workspace_id}/event-actions | covered — test_endpoint_coverage.py (added) |
| GET  /api/workspaces/{workspace_id}/event-actions/{action_id} | covered — test_endpoint_coverage.py (added) |
| PATCH /api/workspaces/{workspace_id}/event-actions/{action_id} | covered — test_endpoint_coverage.py (added) |
| DELETE /api/workspaces/{workspace_id}/event-actions/{action_id} | covered — test_endpoint_coverage.py (added) |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/event-actions | covered — test_event_actions.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/event-actions | covered — test_event_actions.py |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/event-actions/{action_id} | covered — test_event_actions.py |
| PATCH /api/workspaces/{workspace_id}/topics/{topic_id}/event-actions/{action_id} | covered — test_event_actions.py |
| DELETE /api/workspaces/{workspace_id}/topics/{topic_id}/event-actions/{action_id} | covered — test_event_actions.py |
| GET  /api/workspaces/{workspace_id}/notes | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/notes | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/notes/{key} | covered — test_main.py |
| PATCH /api/workspaces/{workspace_id}/notes/{key} | covered — test_main.py |
| DELETE /api/workspaces/{workspace_id}/notes/{key} | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/notes | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/notes | covered — test_main.py |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/notes/{key} | covered — test_main.py |
| PATCH /api/workspaces/{workspace_id}/topics/{topic_id}/notes/{key} | covered — test_main.py |
| DELETE /api/workspaces/{workspace_id}/topics/{topic_id}/notes/{key} | covered — test_main.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/orchestrate/delegate | covered — test_orchestration.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/orchestrate/ask | covered — test_orchestration.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/orchestrate/answer_question | covered — test_orchestration_phase_b.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/orchestrate/submit_result | covered — test_orchestration_phase_b.py |
| POST /api/workspaces/{workspace_id}/topics/{topic_id}/orchestrate/accept_result | covered — test_orchestration_phase_b.py |
| GET  /api/workspaces/{workspace_id}/topics/{topic_id}/orchestrate/tasks | covered — test_orchestration.py |
| WS   /ws/events | allowlisted (WebSocket; cannot be hit via TestClient HTTP) |
| GET  /{full_path:path} | allowlisted (SPA fallback) |
| GET  /health | allowlisted |
| GET  /schema | allowlisted |

## sqlite3.connect Bypass Audit

### Before this branch

78 call sites in test files bypassed `DATABASE_URL` and called `sqlite3.connect(path)` directly.

### Classification

| Category | Count | Action |
|----------|-------|--------|
| `convert` — general tests that should run on all backends | 71 | Converted to `get_connection(db_url)` using the `db_url` fixture |
| `sqlite_only-legit` — tests for SQLite-specific code paths | 7 | Marked `@pytest.mark.sqlite_only` |
| `seeding-only` — helper functions creating test data | 0 | N/A |

### Converted files

- `tests/master/test_event_actions.py` — all direct-dispatch test classes (~60 methods)
- `tests/master/test_main.py` — `stale_db` fixture, `_seed_chunks`, `_count_chunks`, `_get_message`
- `tests/master/test_timezone_audit.py` — `_seed_scheduler_db`, `_read_last_fired_at`, scheduler tests
- `tests/master/test_orchestration.py` — `test_agent_reply_gets_staff_sender_kind`, `test_agent_reply_to_staff_prompt_routes_back`, `test_back05_event_dispatch_stores_sender_kind_staff`, `test_delt11_cycle_detection_walk_logic`
- `tests/master/test_orchestration_phase_b.py` — `test_delegation_chain_no_event_loop`
- `tests/master/test_mqtt_client.py` — `_make_db` helper, `test_record_agent_response_sets_last_responded_at`
- `tests/master/test_notify.py` — `_make_db` helper, `_set_config` helper, all test methods
- `tests/master/test_topic_export.py` — `_insert_message` helper
- `tests/test_master_chunks.py` — `db_path` fixture, `_count_chunks`, `_fetch_chunks`, `_count_messages`
- `tests/test_master_replay.py` — `db_path` fixture, `_insert_chunks`, `_insert_message`

### sqlite_only-legit tests

| Test | Reason |
|------|--------|
| `test_db.py::test_init_db_creates_all_tables` | Uses `sqlite_master` (SQLite-specific system table) |
| `test_db.py::test_init_db_is_idempotent` | Same |
| `test_topic_archiving_veto.py::test_tc07_migration_*` (4 tests) | Tests `init_db` migration path which is SQLite-only |
| `test_orchestration.py::test_backfill_*` (3 tests) | `_migrate_envelope_backfill` takes a native `sqlite3.Connection` |

## Guards

### Guard 4a: sqlite3.connect lint test

**Location:** `tests/master/db/test_lint.py::test_no_bare_sqlite3_connect_in_tests`

Fails if any test file outside `tests/master/db/` calls `sqlite3.connect()` without
the enclosing function or class being marked `@pytest.mark.sqlite_only`.

**Status:** Passing (0 violations).

### Guard 4b: Route coverage check

**Location:** `tests/conftest.py::_install_route_tracker` + `pytest_sessionfinish`

Enabled when `ENFORCE_ROUTE_COVERAGE=1` is set. An ASGI middleware records every
route template hit during the test session. At session end, `pytest_sessionfinish`
sets a non-zero exit status and prints a clear `[route-coverage] FAILED` banner if
any non-allowlisted route was never exercised. Failures are reported as a named
session-level failure, never attributed to an unrelated test.

The route collector matches `scope["route"].path` exactly: it walks `_IncludedRouter`
objects without adding the `include_context.prefix`, which is not present in
`scope["route"].path` for FastAPI 0.160+ routes.

**Activation:** Enforced in CI on both the SQLite and Postgres legs via
`ENFORCE_ROUTE_COVERAGE=1` in `.sre/test-db.sh`. Disabled automatically for
single-test local runs that bypass test-db.sh.

**Status:** All 67 non-allowlisted routes are covered.

## New fixtures

### `db_url` (tests/master/conftest.py)

Returns the configured `DATABASE_URL`. On SQLite creates a per-test file and
calls `init_db`. On non-SQLite returns the pre-configured URL unchanged
(schema already set up by `_non_sqlite_schema_setup`).

### `db_conn` (tests/master/conftest.py)

Yields a `get_connection(db_url)` connection and closes it after the test.

## Test cases

### Happy path

| # | Case | Method | File | Status |
|---|------|--------|------|--------|
| 1 | All existing tests continue to pass on SQLite | all | all | automated |
| 2 | Lint guard finds 0 sqlite3.connect violations | unit | test_lint.py | automated |
| 3 | Endpoint smoke tests return expected status codes | HTTP | test_endpoint_coverage.py | automated |
| 4 | refresh-auth returns 200 with refreshed_at | HTTP | test_endpoint_coverage.py | automated |
| 5 | restart-agent returns 200 | HTTP | test_endpoint_coverage.py | automated |
| 6 | current-staff PATCH updates the topic | HTTP | test_endpoint_coverage.py | automated |
| 7 | cancel message returns 202 | HTTP | test_endpoint_coverage.py | automated |
| 8 | set-default global staff returns 200 with is_default=true | HTTP | test_endpoint_coverage.py | automated |
| 9 | set-default workspace staff returns 200 | HTTP | test_endpoint_coverage.py | automated |
| 10 | PUT topic staff updates session_scope | HTTP | test_endpoint_coverage.py | automated |
| 11 | DELETE topic staff returns 204 | HTTP | test_endpoint_coverage.py | automated |
| 12 | GET system-variables returns list | HTTP | test_endpoint_coverage.py | automated |
| 13 | PATCH system-settings valid tz returns 200 | HTTP | test_endpoint_coverage.py | automated |

### Edge cases / failure modes

| # | Case | Method | File | Status |
|---|------|--------|------|--------|
| 14 | refresh-auth unknown workspace returns 404 | HTTP | test_endpoint_coverage.py | automated |
| 15 | restart-agent unknown workspace returns 404 | HTTP | test_endpoint_coverage.py | automated |
| 16 | current-staff unknown topic returns 404 | HTTP | test_endpoint_coverage.py | automated |
| 17 | set-default unknown staff returns 404 | HTTP | test_endpoint_coverage.py | automated |
| 18 | PATCH system-settings invalid tz returns 422 | HTTP | test_endpoint_coverage.py | automated |
| 19 | DELETE nonexistent topic staff returns 404 | HTTP | test_endpoint_coverage.py | automated |
| 20 | sqlite_only tests are skipped on Postgres leg | infra | pytest marks | automated |

### Non-functional requirements

| # | Case | Status |
|---|------|--------|
| 21 | All non-sqlite_only tests must pass on Postgres CI leg | needs-human — verify CI Postgres job after PR merge |
| 22 | Route coverage guard runs on both sqlite and postgres CI legs | automated — enabled in .sre/test-db.sh |

## Postgres failures to record

No Postgres failures were observed during this work (tests run locally on SQLite
only; Postgres verification via CI). Any failures discovered on the CI Postgres
leg should be recorded here with: test name, error, root cause (file:line), and
whether the test is expected to pass or to be marked sqlite_only.

## Non-goals

- Modifying production code (src/) to fix Postgres incompatibilities — that is
  an engineer task.
