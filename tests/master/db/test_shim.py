"""TC-02, TC-03, TC-04: get_connection() shim behaviour.

Tests that the post-M0 get_connection() shim:
  - Accepts SQLAlchemy text() objects with named :param bindings.
  - Returns rows that support name-keyed access (row["col"]).
  - Supports fetchone() / fetchall().
  - Supports commit() / rollback().
  - Works as a context manager.
  - Enforces foreign keys on SQLite (equivalent to PRAGMA foreign_keys = ON).

All tests are skipped if the M0 db/ package (engine function) is not yet
available.
"""
from __future__ import annotations

import pytest

from tests.master.db.conftest import SKIP_NO_M0

pytestmark = [SKIP_NO_M0]

# We also need sqlalchemy.text; import after skip guard so the test
# collection itself doesn't fail without sqlalchemy installed.
sqlalchemy = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_conn(db_url: str):
    """Open a shim connection for the given SQLite URL.

    After M0, get_connection() accepts a DATABASE_URL string (or no args,
    using the env-configured URL). The design says the shim behaves like
    today's sqlite3.Connection for the subset used by call sites. We call
    it with a sqlite URL here.
    """
    from src.master.db import get_connection
    return get_connection(db_url)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_db(tmp_path):
    """Return a URL to a fresh SQLite DB initialised via init_db()."""
    from src.master.db import init_db
    db_path = str(tmp_path / "shim_test.db")
    init_db(db_path)
    return f"sqlite+pysqlite:///{db_path}"


# ---------------------------------------------------------------------------
# TC-02: text() with named params
# ---------------------------------------------------------------------------

class TestTextNamedParams:
    def test_simple_select(self, fresh_db):
        """conn.execute(text('SELECT 1')) returns a result without error."""
        from sqlalchemy import text
        conn = _open_conn(fresh_db)
        try:
            result = conn.execute(text("SELECT 1"))
            row = result.fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_insert_with_named_params(self, fresh_db):
        """INSERT with text() and named :param dict succeeds."""
        from sqlalchemy import text
        conn = _open_conn(fresh_db)
        try:
            conn.execute(
                text(
                    "INSERT INTO workspaces"
                    " (id, name, repo_url, created_at)"
                    " VALUES (:id, :name, :repo_url, :created_at)"
                ),
                {
                    "id": "ws-shim-01",
                    "name": "shim-test",
                    "repo_url": "https://github.com/x/y",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            )
            conn.commit()
        finally:
            conn.close()

    def test_select_with_named_param(self, fresh_db):
        """SELECT with named :param returns the correct row."""
        from sqlalchemy import text
        # Seed first
        conn = _open_conn(fresh_db)
        conn.execute(
            text(
                "INSERT INTO workspaces (id, name, repo_url, created_at)"
                " VALUES (:id, :name, :repo_url, :created_at)"
            ),
            {
                "id": "ws-shim-02",
                "name": "lookup-test",
                "repo_url": "https://github.com/a/b",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
        conn.commit()
        conn.close()

        conn = _open_conn(fresh_db)
        try:
            result = conn.execute(
                text("SELECT id FROM workspaces WHERE id = :id"),
                {"id": "ws-shim-02"},
            )
            row = result.fetchone()
            assert row is not None
            assert row["id"] == "ws-shim-02"
        finally:
            conn.close()

    def test_fetchall_returns_list(self, fresh_db):
        """fetchall() returns a list of rows."""
        from sqlalchemy import text
        conn = _open_conn(fresh_db)
        try:
            result = conn.execute(text("SELECT id FROM workspaces"))
            rows = result.fetchall()
            assert isinstance(rows, list)
        finally:
            conn.close()

    def test_row_name_keyed_access(self, fresh_db):
        """Row objects returned by the shim support dict-style name-keyed access."""
        from sqlalchemy import text
        # Seed a workspace.
        conn = _open_conn(fresh_db)
        conn.execute(
            text(
                "INSERT INTO workspaces (id, name, repo_url, created_at)"
                " VALUES (:id, :name, :repo_url, :created_at)"
            ),
            {
                "id": "ws-shim-03",
                "name": "row-access",
                "repo_url": "https://github.com/c/d",
                "created_at": "2026-01-02T00:00:00Z",
            },
        )
        conn.commit()
        conn.close()

        conn = _open_conn(fresh_db)
        try:
            result = conn.execute(
                text("SELECT id, name, repo_url FROM workspaces WHERE id = :id"),
                {"id": "ws-shim-03"},
            )
            row = result.fetchone()
            assert row is not None
            # Must support dict/mapping-style access.
            assert row["id"] == "ws-shim-03"
            assert row["name"] == "row-access"
            assert row["repo_url"] == "https://github.com/c/d"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TC-03: context manager, commit, rollback
# ---------------------------------------------------------------------------

class TestTransactionControl:
    def test_context_manager_commits_on_clean_exit(self, fresh_db):
        """Rows inserted inside 'with get_connection()' block are visible after exit."""
        from sqlalchemy import text
        from src.master.db import get_connection
        with get_connection(fresh_db) as conn:
            conn.execute(
                text(
                    "INSERT INTO workspaces (id, name, repo_url, created_at)"
                    " VALUES (:id, :name, :repo_url, :created_at)"
                ),
                {
                    "id": "ws-ctx-01",
                    "name": "ctx-test",
                    "repo_url": "https://github.com/e/f",
                    "created_at": "2026-01-03T00:00:00Z",
                },
            )

        conn2 = _open_conn(fresh_db)
        try:
            result = conn2.execute(
                text("SELECT id FROM workspaces WHERE id = :id"),
                {"id": "ws-ctx-01"},
            )
            assert result.fetchone() is not None, (
                "Row inserted in context manager was not committed on exit"
            )
        finally:
            conn2.close()

    def test_rollback_discards_inserts(self, fresh_db):
        """conn.rollback() discards uncommitted inserts."""
        from sqlalchemy import text
        conn = _open_conn(fresh_db)
        try:
            conn.execute(
                text(
                    "INSERT INTO workspaces (id, name, repo_url, created_at)"
                    " VALUES (:id, :name, :repo_url, :created_at)"
                ),
                {
                    "id": "ws-rollback-01",
                    "name": "rollback-test",
                    "repo_url": "https://github.com/g/h",
                    "created_at": "2026-01-04T00:00:00Z",
                },
            )
            conn.rollback()
        finally:
            conn.close()

        conn2 = _open_conn(fresh_db)
        try:
            result = conn2.execute(
                text("SELECT id FROM workspaces WHERE id = :id"),
                {"id": "ws-rollback-01"},
            )
            assert result.fetchone() is None, (
                "Row should not be visible after rollback"
            )
        finally:
            conn2.close()


# ---------------------------------------------------------------------------
# TC-04: foreign-key enforcement on SQLite
# ---------------------------------------------------------------------------

class TestForeignKeyEnforcement:
    def test_fk_violation_raises(self, fresh_db):
        """Inserting a row that violates a FK raises an error on SQLite."""
        from sqlalchemy import text, exc

        conn = _open_conn(fresh_db)
        try:
            with pytest.raises((exc.IntegrityError, Exception)) as exc_info:
                conn.execute(
                    text(
                        "INSERT INTO topics"
                        " (id, workspace_id, subject, branch_name,"
                        "  worktree_path, created_at)"
                        " VALUES (:id, :ws_id, :subject, :branch, :path, :created_at)"
                    ),
                    {
                        "id": "topic-fk-01",
                        "ws_id": "nonexistent-workspace-id",
                        "subject": "FK violation test",
                        "branch": "feat/test",
                        "path": "/tmp/test",
                        "created_at": "2026-01-05T00:00:00Z",
                    },
                )
                conn.commit()
            # Verify it's actually a FK-related error.
            error_msg = str(exc_info.value).lower()
            assert any(kw in error_msg for kw in ("foreign key", "integrity", "constraint")), (
                f"Expected a FK/integrity error, got: {exc_info.value}"
            )
        finally:
            conn.close()
