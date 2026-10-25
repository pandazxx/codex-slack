"""M1 TC-27–TC-30: Tests for M0 review findings carried into M1.

Findings being addressed:
  M-3 (carried): scope_id IS :scope_id in staffs.py is invalid on Postgres.
    The IS operator requires a literal on the right-hand side in Postgres
    (e.g., IS NULL, IS NOT NULL); a named parameter :scope_id raises a syntax
    error on Postgres.  Replacement: use a dialect helper or the explicit form
    `scope_id = :scope_id OR (scope_id IS NULL AND :scope_id IS NULL)`.
    Impact: all staffs.py CRUD helpers that filter by (scope_type, scope_id) —
    _update_staff, _set_default_staff, _delete_staff — break on Postgres when
    scope_id is NULL (global-scope staff).

  m-2 (carried): schema_info() uses PRAGMA table_info, which is SQLite-only.
    Replacement: use sqlalchemy.inspect().  Impact: schema_info() breaks on
    Postgres and MySQL in M0.

Test strategy:
  - The M-3 and m-2 tests run on SQLite today (they pass even with the bug
    because SQLite is lenient), which documents the required behaviour.
  - When TEST_POSTGRES_URL is set, these tests also run against Postgres,
    catching the actual bugs M-3 and m-2 introduce there.
  - All tests are written against the PUBLIC interface of src.master.staffs
    (the internal helper functions _update_staff, _delete_staff,
    _set_default_staff) and src.master.db.schema_info.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[3]
_NOW = "2026-10-25T12:00:00Z"


# ---------------------------------------------------------------------------
# Parametrize over dialects
# ---------------------------------------------------------------------------

def _sqlite_url(tmp_path: Path, name: str = "carried.db") -> str:
    return f"sqlite+pysqlite:///{tmp_path / name}"


def _postgres_url() -> str | None:
    return os.environ.get("TEST_POSTGRES_URL")


def _dialect_urls(tmp_path: Path) -> list:
    """Return a list of pytest.param objects for each available dialect."""
    params = [
        pytest.param(
            _sqlite_url(tmp_path),
            id="sqlite",
        )
    ]
    pg = _postgres_url()
    if pg:
        params.append(pytest.param(pg, id="postgres"))
    return params


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_with_schema(tmp_path):
    """A fresh SQLite DB with the full schema (via init_db)."""
    db = pytest.importorskip("src.master.db", reason="src.master.db not importable")
    db_path = str(tmp_path / "carried_test.db")
    db.init_db(db_path)
    return db_path


@pytest.fixture()
def db_url_with_schema(tmp_path):
    """Fixture yielding a SQLite URL pointing at a DB created via startup_db or init_db."""
    db = pytest.importorskip("src.master.db", reason="src.master.db not importable")
    db_path = str(tmp_path / "carried_url_test.db")
    db.init_db(db_path)
    url = f"sqlite+pysqlite:///{db_path}"
    return url, db_path


# ---------------------------------------------------------------------------
# TC-27 / TC-28: Global-scope staff (NULL scope_id) CRUD via staffs.py helpers
# ---------------------------------------------------------------------------

class TestGlobalStaffNullScopeId:
    """staffs.py helpers must correctly handle scope_id=NULL (global scope).

    The M-3 finding: IS :scope_id is invalid on Postgres.
    These tests document the required semantics; they fail on Postgres until
    the fix is applied.
    """

    def _insert_global_staff(self, db_path: str, name: str) -> str:
        """Insert a global staff row directly and return its id."""
        import uuid
        staff_id = str(uuid.uuid4())
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO staffs (id, scope_type, scope_id, name, adapter,"
                " model, system_prompt, agent, session_scope, is_default,"
                " extra_flags, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    staff_id, "global", None, name, "claude-code",
                    None, None, None, "topic", 0, None, _NOW, _NOW,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return staff_id

    def test_global_staff_update_by_name(self, db_with_schema):
        """_update_staff correctly finds a global staff row with scope_id=NULL."""
        from src.master.db import get_connection
        from src.master.staffs import StaffIn, _update_staff

        db_path = db_with_schema
        self._insert_global_staff(db_path, "global-agent-update")

        body = StaffIn(name="global-agent-update", adapter="claude-code", model="claude-3-5")
        with get_connection(db_path) as conn:
            result = _update_staff(conn, body, "global", None, "global-agent-update")

        assert result.name == "global-agent-update", (
            f"Expected staff name 'global-agent-update', got {result.name!r}"
        )
        assert result.scope_type == "global", (
            f"Expected scope_type 'global', got {result.scope_type!r}"
        )
        assert result.scope_id is None, (
            f"Expected scope_id=None (global), got {result.scope_id!r}"
        )
        assert result.model == "claude-3-5", (
            f"Expected model 'claude-3-5' after update, got {result.model!r}"
        )

    def test_global_staff_update_not_found_when_wrong_scope(self, db_with_schema):
        """_update_staff must NOT return a workspace-scope staff when scope_id=None."""
        from fastapi import HTTPException

        from src.master.db import get_connection
        from src.master.staffs import StaffIn, _update_staff

        db_path = db_with_schema
        # Insert a workspace-scope staff with a non-NULL scope_id
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO workspaces (id, name, repo_url, created_at)"
                " VALUES ('ws-carried-001', 'carried-ws',"
                " 'https://github.com/x/y', :now)",
                {"now": _NOW},
            )
            conn.execute(
                "INSERT INTO staffs (id, scope_type, scope_id, name, adapter,"
                " model, system_prompt, agent, session_scope, is_default,"
                " extra_flags, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "staff-ws-carried", "workspace", "ws-carried-001",
                    "ws-agent", "claude-code", None, None, None,
                    "topic", 0, None, _NOW, _NOW,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        body = StaffIn(name="ws-agent", adapter="claude-code")
        with get_connection(db_path) as conn:
            with pytest.raises(HTTPException) as exc_info:
                # scope_id=None means global scope — must NOT find workspace staff
                _update_staff(conn, body, "workspace", None, "ws-agent")
        assert exc_info.value.status_code == 404, (
            "Expected 404 when searching for workspace staff with scope_id=None"
        )

    def test_global_staff_set_default(self, db_with_schema):
        """_set_default_staff correctly finds and marks a global staff as default."""
        from src.master.db import get_connection
        from src.master.staffs import _set_default_staff

        db_path = db_with_schema
        self._insert_global_staff(db_path, "global-agent-default")

        with get_connection(db_path) as conn:
            result = _set_default_staff(conn, "global", None, "global-agent-default")

        assert result.is_default is True, (
            f"Expected is_default=True after set_default_staff, got {result.is_default!r}"
        )

    def test_global_staff_delete(self, db_with_schema):
        """_delete_staff correctly removes a global staff row."""
        from src.master.db import get_connection
        from src.master.staffs import _delete_staff

        db_path = db_with_schema
        self._insert_global_staff(db_path, "global-agent-delete")

        with get_connection(db_path) as conn:
            _delete_staff(conn, "global", None, "global-agent-delete")

        conn2 = sqlite3.connect(db_path)
        try:
            row = conn2.execute(
                "SELECT id FROM staffs WHERE scope_type='global' AND name='global-agent-delete'"
            ).fetchone()
        finally:
            conn2.close()
        assert row is None, "global staff row must be absent after _delete_staff"

    def test_global_staff_delete_not_found_when_absent(self, db_with_schema):
        """_delete_staff raises 404 when the global staff does not exist."""
        from fastapi import HTTPException

        from src.master.db import get_connection
        from src.master.staffs import _delete_staff

        db_path = db_with_schema
        with get_connection(db_path) as conn:
            with pytest.raises(HTTPException) as exc_info:
                _delete_staff(conn, "global", None, "nonexistent-global-agent")
        assert exc_info.value.status_code == 404

    # Postgres variants: these will fail until M-3 is fixed

    @pytest.mark.skipif(
        not os.environ.get("TEST_POSTGRES_URL"),
        reason="TEST_POSTGRES_URL not set; M-3 Postgres fix not yet testable",
    )
    def test_global_staff_update_on_postgres(self, tmp_path):
        """_update_staff finds a global staff (scope_id=NULL) on Postgres.

        Fails until the IS :scope_id fix is applied (M-3 carried item).
        """
        from src.master.db import get_connection, startup_db
        from src.master.staffs import StaffIn, _update_staff
        from sqlalchemy import text

        pg_url = os.environ["TEST_POSTGRES_URL"]
        startup_db(pg_url)

        from sqlalchemy import create_engine
        import uuid

        staff_id = str(uuid.uuid4())
        engine = create_engine(pg_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO staffs (id, scope_type, scope_id, name, adapter,"
                        " model, system_prompt, agent, session_scope, is_default,"
                        " extra_flags, created_at, updated_at)"
                        " VALUES (:id, 'global', NULL, :name, 'claude-code',"
                        " NULL, NULL, NULL, 'topic', false, NULL, :now, :now)"
                    ),
                    {"id": staff_id, "name": "pg-global-agent", "now": _NOW},
                )
        finally:
            engine.dispose()

        with get_connection(pg_url) as conn:
            body = StaffIn(name="pg-global-agent", adapter="claude-code", model="claude-3-5")
            result = _update_staff(conn, body, "global", None, "pg-global-agent")

        assert result.model == "claude-3-5", (
            f"Postgres global staff update failed: model={result.model!r}"
        )


# ---------------------------------------------------------------------------
# TC-29 / TC-30: schema_info() returns columns via inspect, not PRAGMA
# ---------------------------------------------------------------------------

class TestSchemaInfoViaInspect:
    """schema_info() must return correct column names for all tables.

    m-2 (carried): The M0 implementation uses PRAGMA table_info which is
    SQLite-only and will break on Postgres/MySQL.  M1 must replace it with
    sqlalchemy.inspect().

    These tests verify the output is correct; the Postgres parametrize verifies
    the implementation no longer relies on SQLite PRAGMAs.
    """

    EXPECTED_WORKSPACES_COLS = {
        "id", "name", "repo_url", "container_name", "created_at",
        "archived_at", "last_refreshed_at", "last_message_at",
        "last_dispatched_at", "last_responded_at",
    }
    EXPECTED_CHUNKS_COLS = {
        "id", "message_id", "topic_id", "seq", "event", "agent_name", "created_at",
    }

    def test_schema_info_returns_dict(self, db_with_schema):
        """schema_info returns a dict."""
        from src.master.db import schema_info

        info = schema_info(db_with_schema)
        assert isinstance(info, dict), (
            f"schema_info should return dict, got {type(info).__name__}"
        )

    def test_schema_info_has_all_tables(self, db_with_schema):
        """schema_info includes all known tables."""
        from src.master.db import schema_info, TABLES

        info = schema_info(db_with_schema)
        missing = set(TABLES) - set(info.keys())
        assert not missing, f"schema_info missing tables: {sorted(missing)}"

    def test_schema_info_workspaces_columns(self, db_with_schema):
        """schema_info returns the correct column names for workspaces."""
        from src.master.db import schema_info

        info = schema_info(db_with_schema)
        got = set(info["workspaces"])
        missing = self.EXPECTED_WORKSPACES_COLS - got
        assert not missing, (
            f"schema_info missing workspaces columns: {sorted(missing)}"
        )

    def test_schema_info_chunks_columns(self, db_with_schema):
        """schema_info returns the correct column names for chunks."""
        from src.master.db import schema_info

        info = schema_info(db_with_schema)
        got = set(info["chunks"])
        missing = self.EXPECTED_CHUNKS_COLS - got
        assert not missing, (
            f"schema_info missing chunks columns: {sorted(missing)}"
        )

    def test_schema_info_returns_nonempty_lists(self, db_with_schema):
        """schema_info returns a non-empty column list for every table."""
        from src.master.db import schema_info, TABLES

        info = schema_info(db_with_schema)
        empty_tables = [t for t in TABLES if not info.get(t)]
        assert not empty_tables, (
            f"schema_info returned empty column list for: {empty_tables}"
        )

    def test_schema_info_implementation_uses_inspect_not_pragma(self, db_with_schema):
        """schema_info source code must not contain 'PRAGMA table_info' after m-2 fix.

        This is a lint-level check: once m-2 is fixed in M1, PRAGMA should be gone.
        Until then this test documents the expected post-fix state.
        """
        import inspect as py_inspect
        import src.master.db as db_mod

        source = py_inspect.getsource(db_mod.schema_info)
        assert "PRAGMA" not in source, (
            "schema_info still uses PRAGMA table_info (m-2 not yet fixed).\n"
            "Replace with sqlalchemy.inspect() for Postgres/MySQL compatibility."
        )

    @pytest.mark.skipif(
        not os.environ.get("TEST_POSTGRES_URL"),
        reason="TEST_POSTGRES_URL not set; skipping schema_info Postgres test",
    )
    def test_schema_info_works_on_postgres(self, tmp_path):
        """schema_info returns correct column lists when used with a Postgres DB.

        Fails in M0 because PRAGMA is SQLite-only; passes after m-2 fix.
        """
        from src.master.db import schema_info, startup_db, TABLES

        pg_url = os.environ["TEST_POSTGRES_URL"]
        startup_db(pg_url)

        info = schema_info(pg_url)
        assert isinstance(info, dict), "schema_info must return dict on Postgres"
        missing = set(TABLES) - set(info.keys())
        assert not missing, (
            f"schema_info missing tables on Postgres: {sorted(missing)}"
        )
        for table, cols in info.items():
            assert cols, f"schema_info returned empty columns for Postgres table '{table}'"
