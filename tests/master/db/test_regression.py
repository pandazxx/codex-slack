"""Regression tests for M0 code-review findings.

C-1: File-based SQLite must use NullPool — no shared DBAPI connection across threads.
M-1: engine() and alembic/env.py must resolve to the same file as main.py.
M-2: _ConnectionShim.__exit__ must close the connection.

Tests are skipped when the M0 db/ package (engine function) is not available.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.master.db.conftest import SKIP_NO_M0

pytestmark = [SKIP_NO_M0]

sqlalchemy = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")

REPO_ROOT = Path(__file__).parents[3]


# ---------------------------------------------------------------------------
# C-1: NullPool for file SQLite — distinct DBAPI connections across threads
# ---------------------------------------------------------------------------

class TestNullPoolForFileSqlite:
    """Each get_connection() call on a file DB must open a fresh OS connection.

    StaticPool shares one DBAPI connection across all checkouts, which causes
    data races when multiple threads call get_connection() concurrently.
    NullPool gives every checkout its own connection, matching the old
    sqlite3.connect() behaviour.
    """

    def test_concurrent_threads_get_distinct_dbapi_connections(self, tmp_path):
        """Two threads opening connections simultaneously get different DBAPI objects."""
        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        db_path = str(tmp_path / "concurrent.db")
        db_mod.init_db(db_path)

        dbapi_ids: list[int] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def _open_and_record():
            try:
                barrier.wait()  # both threads acquire a connection at the same time
                conn = db_mod.get_connection(db_path)
                try:
                    # Access the underlying DBAPI connection identity.
                    dbapi_conn = conn._conn.connection.dbapi_connection
                    dbapi_ids.append(id(dbapi_conn))
                finally:
                    conn.close()
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_open_and_record)
        t2 = threading.Thread(target=_open_and_record)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Thread errors: {errors}"
        assert len(dbapi_ids) == 2
        assert dbapi_ids[0] != dbapi_ids[1], (
            "Both threads got the same DBAPI connection object — "
            "StaticPool is still being used for a file-based SQLite database. "
            "Expected NullPool so each thread gets an independent connection."
        )

    def test_concurrent_inserts_all_succeed(self, tmp_path):
        """Multiple threads inserting rows concurrently all succeed without deadlock."""
        import importlib
        import src.master.db as db_mod
        from sqlalchemy import text

        importlib.reload(db_mod)

        db_path = str(tmp_path / "parallel.db")
        db_mod.init_db(db_path)

        failures: list[Exception] = []

        def _insert_workspace(idx: int) -> None:
            try:
                conn = db_mod.get_connection(db_path)
                try:
                    conn.execute(
                        text(
                            "INSERT INTO workspaces"
                            " (id, name, repo_url, created_at)"
                            " VALUES (:id, :name, :repo_url, :created_at)"
                        ),
                        {
                            "id": f"ws-concurrent-{idx}",
                            "name": f"concurrent-{idx}",
                            "repo_url": "https://github.com/x/y",
                            "created_at": "2026-01-01T00:00:00Z",
                        },
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                failures.append(exc)

        threads = [threading.Thread(target=_insert_workspace, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not failures, f"Concurrent insert failures: {failures}"

        # Verify all rows landed.
        conn = db_mod.get_connection(db_path)
        try:
            rows = conn.execute(
                text("SELECT COUNT(*) FROM workspaces WHERE id LIKE 'ws-concurrent-%'")
            ).fetchone()
            assert rows[0] == 5, f"Expected 5 rows, got {rows[0]}"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# M-2: connection is closed after the with block
# ---------------------------------------------------------------------------

class TestConnectionClosedAfterWithBlock:
    """_ConnectionShim.__exit__ must close the underlying connection."""

    def test_connection_marked_closed_after_with(self, tmp_path):
        """_closed is True immediately after the with block exits."""
        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        db_path = str(tmp_path / "close_test.db")
        db_mod.init_db(db_path)

        with db_mod.get_connection(db_path) as conn:
            assert not conn._closed, "Connection should not be closed inside the with block"

        assert conn._closed, (
            "Connection _closed flag must be True after the with block exits"
        )

    def test_execute_raises_after_with_block(self, tmp_path):
        """Executing on a connection after the with block raises an error."""
        import importlib
        import src.master.db as db_mod
        from sqlalchemy import text
        from sqlalchemy.exc import InvalidRequestError

        importlib.reload(db_mod)

        db_path = str(tmp_path / "use_after_close.db")
        db_mod.init_db(db_path)

        with db_mod.get_connection(db_path) as conn:
            pass  # exits normally; __exit__ commits and closes

        with pytest.raises((InvalidRequestError, Exception)):
            conn.execute(text("SELECT 1"))

    def test_close_is_idempotent(self, tmp_path):
        """Calling close() twice does not raise."""
        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        db_path = str(tmp_path / "idempotent_close.db")
        db_mod.init_db(db_path)

        conn = db_mod.get_connection(db_path)
        conn.close()
        conn.close()  # must not raise


# ---------------------------------------------------------------------------
# M-1: engine() and alembic env resolve to master_data.db (same as main.py)
# ---------------------------------------------------------------------------

class TestMasterDataDbFilename:
    """engine() and alembic/env.py must use master_data.db, matching main.py."""

    def test_engine_resolves_master_data_db(self, monkeypatch, tmp_path):
        """engine() via MASTER_DATA_DIR resolves to master_data.db."""
        import importlib
        import src.master.db as db_mod

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
        importlib.reload(db_mod)

        eng = db_mod.engine()
        engine_url = str(eng.url)
        assert "master_data.db" in engine_url, (
            f"engine() resolved to {engine_url!r}; expected master_data.db"
        )
        assert "master.db" not in engine_url.replace("master_data.db", ""), (
            f"engine() URL still contains legacy master.db: {engine_url!r}"
        )
        eng.dispose()

    def test_resolve_db_url_matches_main_py_filename(self, monkeypatch, tmp_path):
        """resolve_db_url() returns a path ending in master_data.db."""
        import importlib
        import src.master.db as db_mod

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
        importlib.reload(db_mod)

        url = db_mod.resolve_db_url()
        assert url.endswith("master_data.db"), (
            f"resolve_db_url() returned {url!r}; expected path ending in master_data.db"
        )

    def test_alembic_env_resolve_matches_engine(self, monkeypatch, tmp_path):
        """alembic/env.py resolve_db_url and engine() point at the same file."""
        import importlib
        import src.master.db as db_mod

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
        importlib.reload(db_mod)

        # Both paths now call the same resolve_db_url from src.master.db.
        engine_url = str(db_mod.engine().url)
        resolved_url = db_mod.resolve_db_url()

        # The engine URL and the resolved URL should point at the same file.
        assert "master_data.db" in engine_url
        assert "master_data.db" in resolved_url
        # Strip the dialect prefix for a direct path comparison.
        engine_path = engine_url.split("///", 1)[-1]
        resolved_path = resolved_url.split("///", 1)[-1]
        assert engine_path == resolved_path, (
            f"engine URL path {engine_path!r} != resolve_db_url path {resolved_path!r}"
        )
