"""M1 TC-24–TC-26: SQLite upgrade safety net tests (design doc §11).

When the default DATABASE_URL switches to Postgres, operators who restart master
without setting DATABASE_URL would silently lose their SQLite data.  The safety
net prevents this by refusing to start when:
  - DATABASE_URL is unset, AND
  - ${MASTER_DATA_DIR}/master_data.db exists, AND
  - the file is non-empty AND contains a 'workspaces' table.

The three outcomes (§11):
  1. DATABASE_URL set explicitly   → use it; no check (operator expressed intent)
  2. DATABASE_URL unset + SQLite   → refuse with a message naming:
       - the path to the SQLite file
       - option (a): export DATABASE_URL=sqlite+pysqlite:///.../master_data.db
       - option (b): scripts/db.sh snapshot-and-migrate ...
       - reference to migrate-sqlite-to-postgres.md
  3. DATABASE_URL unset + no file  → use the Postgres default URL

Interface contract (must be satisfied before these tests go green):

  src.master.db.sqlite_safety_check(data_dir: str) -> None
    - Reads DATABASE_URL from the environment.
    - If DATABASE_URL is set: returns immediately (no-op).
    - If DATABASE_URL is unset and ${data_dir}/master_data.db is present
      and contains a 'workspaces' table: raises SystemExit(1) with a message
      containing:
        * the full path to master_data.db
        * "(a)" (option label for Keep SQLite path)
        * "(b)" (option label for Migrate to Postgres path)
        * "DATABASE_URL" (mentioned in both option descriptions)
    - If DATABASE_URL is unset and no SQLite file: returns normally.

  src.master.db.resolve_db_url() behaviour change in M1:
    - When DATABASE_URL is unset and MASTER_DATA_DIR is set to a dir
      that does NOT contain master_data.db, the returned URL should start
      with "postgresql+psycopg://" (Postgres default).

All tests skip when sqlite_safety_check is absent from src.master.db
(M1 not implemented yet).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[3]


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

def _has_safety_check() -> bool:
    try:
        import src.master.db as db_mod
        return hasattr(db_mod, "sqlite_safety_check")
    except Exception:
        return False


SKIP_NO_SAFETY_CHECK = pytest.mark.skipif(
    not _has_safety_check(),
    reason=(
        "src.master.db.sqlite_safety_check not available — "
        "M1 SQLite safety-net not yet implemented"
    ),
)

pytestmark = [SKIP_NO_SAFETY_CHECK]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_sqlite_with_workspaces(path: str) -> None:
    """Create a real SQLite DB file with a workspaces table and one row."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO workspaces (id, name) VALUES ('ws-safety-001', 'test')"
        )
        conn.commit()
    finally:
        conn.close()


def _run_safety_check_subprocess(
    data_dir: str,
    *,
    database_url: str | None = None,
) -> tuple[int, str]:
    """Run sqlite_safety_check(data_dir) in a child process.

    Returns (exit_code, combined_output).
    """
    env = {k: v for k, v in os.environ.items()}
    env.pop("DATABASE_URL", None)
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    env.pop("MASTER_DATA_DIR", None)

    script = (
        "import sys; "
        f"sys.path.insert(0, str(__import__('pathlib').Path('.').resolve())); "
        "from src.master.db import sqlite_safety_check; "
        f"sqlite_safety_check({data_dir!r})"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# TC-24: DATABASE_URL unset + SQLite file with workspaces → refuses
# ---------------------------------------------------------------------------

class TestSafetyNetRefusesWithSqliteFile:
    """Master refuses to start when SQLite data would be silently abandoned."""

    def test_refuses_when_sqlite_file_exists(self, tmp_path, monkeypatch):
        """sqlite_safety_check refuses when master_data.db exists and DATABASE_URL is unset."""
        from src.master.db import sqlite_safety_check

        data_dir = str(tmp_path)
        db_path = str(tmp_path / "master_data.db")
        _make_sqlite_with_workspaces(db_path)

        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            sqlite_safety_check(data_dir)

        assert exc_info.value.code != 0, (
            f"Expected non-zero exit, got {exc_info.value.code!r}"
        )

    def test_exit_code_is_nonzero_via_subprocess(self, tmp_path):
        """sqlite_safety_check exits non-zero when SQLite file is present."""
        data_dir = str(tmp_path)
        _make_sqlite_with_workspaces(str(tmp_path / "master_data.db"))

        exit_code, output = _run_safety_check_subprocess(data_dir)
        assert exit_code != 0, (
            f"Expected non-zero exit code, got {exit_code}:\n{output}"
        )

    def test_error_message_contains_sqlite_path(self, tmp_path):
        """The refusal message names the path to master_data.db."""
        data_dir = str(tmp_path)
        db_path = str(tmp_path / "master_data.db")
        _make_sqlite_with_workspaces(db_path)

        exit_code, output = _run_safety_check_subprocess(data_dir)
        assert exit_code != 0
        assert "master_data.db" in output, (
            f"Expected 'master_data.db' in error output, got:\n{output}"
        )

    def test_error_message_contains_remedy_a(self, tmp_path):
        """The refusal message includes option (a): keep SQLite."""
        data_dir = str(tmp_path)
        _make_sqlite_with_workspaces(str(tmp_path / "master_data.db"))

        exit_code, output = _run_safety_check_subprocess(data_dir)
        assert exit_code != 0
        assert "(a)" in output, (
            f"Expected '(a)' (Keep SQLite option) in error message, got:\n{output}"
        )

    def test_error_message_contains_remedy_b(self, tmp_path):
        """The refusal message includes option (b): migrate to Postgres."""
        data_dir = str(tmp_path)
        _make_sqlite_with_workspaces(str(tmp_path / "master_data.db"))

        exit_code, output = _run_safety_check_subprocess(data_dir)
        assert exit_code != 0
        assert "(b)" in output, (
            f"Expected '(b)' (Migrate to Postgres option) in error message, got:\n{output}"
        )

    def test_error_message_contains_database_url(self, tmp_path):
        """The refusal message mentions DATABASE_URL so the operator knows what to set."""
        data_dir = str(tmp_path)
        _make_sqlite_with_workspaces(str(tmp_path / "master_data.db"))

        exit_code, output = _run_safety_check_subprocess(data_dir)
        assert exit_code != 0
        assert "DATABASE_URL" in output, (
            f"Expected 'DATABASE_URL' in error message, got:\n{output}"
        )

    def test_empty_sqlite_file_does_not_trigger(self, tmp_path, monkeypatch):
        """A zero-byte SQLite file does not trigger the safety net (not a real DB)."""
        from src.master.db import sqlite_safety_check

        data_dir = str(tmp_path)
        db_path = tmp_path / "master_data.db"
        db_path.write_bytes(b"")  # empty file

        monkeypatch.delenv("DATABASE_URL", raising=False)
        sqlite_safety_check(data_dir)  # must not raise

    def test_sqlite_without_workspaces_table_does_not_trigger(self, tmp_path, monkeypatch):
        """A SQLite file without a 'workspaces' table does not trigger the safety net."""
        from src.master.db import sqlite_safety_check

        data_dir = str(tmp_path)
        conn = sqlite3.connect(str(tmp_path / "master_data.db"))
        try:
            conn.execute("CREATE TABLE unrelated (id TEXT)")
            conn.commit()
        finally:
            conn.close()

        monkeypatch.delenv("DATABASE_URL", raising=False)
        sqlite_safety_check(data_dir)  # must not raise


# ---------------------------------------------------------------------------
# TC-25: DATABASE_URL unset + no SQLite file → uses Postgres default
# ---------------------------------------------------------------------------

class TestSafetyNetNoSqliteFile:
    """When no SQLite file exists, master proceeds with the Postgres default."""

    def test_no_error_when_sqlite_absent(self, tmp_path, monkeypatch):
        """sqlite_safety_check does not raise when master_data.db is absent."""
        from src.master.db import sqlite_safety_check

        data_dir = str(tmp_path)  # empty dir — no master_data.db
        monkeypatch.delenv("DATABASE_URL", raising=False)
        sqlite_safety_check(data_dir)  # must not raise

    def test_default_url_is_postgres_when_no_sqlite_file(self, tmp_path, monkeypatch):
        """resolve_db_url() returns a Postgres URL when DATABASE_URL is unset and no SQLite file.

        This test will fail in M0 (where the default is still SQLite) and
        pass once M1 switches the default to Postgres.
        """
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))  # no master_data.db here

        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        try:
            url = db_mod.resolve_db_url()
        except ValueError:
            pytest.skip("resolve_db_url raises ValueError (M1 not yet providing Postgres default)")

        assert url.startswith("postgresql+psycopg://"), (
            f"Expected Postgres default URL when no SQLite file present, got: {url!r}"
        )


# ---------------------------------------------------------------------------
# TC-26: Explicit DATABASE_URL always wins (no safety-net override)
# ---------------------------------------------------------------------------

class TestSafetyNetExplicitUrlWins:
    """When DATABASE_URL is set explicitly, the safety net does not fire."""

    def test_explicit_postgres_url_wins(self, tmp_path, monkeypatch):
        """An explicit Postgres DATABASE_URL bypasses the safety check entirely."""
        from src.master.db import sqlite_safety_check

        data_dir = str(tmp_path)
        _make_sqlite_with_workspaces(str(tmp_path / "master_data.db"))

        # Operator has explicitly set DATABASE_URL → trust their intent
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://master:secret@localhost:5432/master",
        )
        sqlite_safety_check(data_dir)  # must not raise

    def test_explicit_sqlite_url_wins(self, tmp_path, monkeypatch):
        """An explicit sqlite DATABASE_URL bypasses the safety check (operator opted in)."""
        from src.master.db import sqlite_safety_check

        data_dir = str(tmp_path)
        db_path = str(tmp_path / "master_data.db")
        _make_sqlite_with_workspaces(db_path)

        monkeypatch.setenv(
            "DATABASE_URL",
            f"sqlite+pysqlite:///{db_path}",
        )
        sqlite_safety_check(data_dir)  # must not raise

    def test_explicit_mysql_url_wins(self, tmp_path, monkeypatch):
        """An explicit MySQL DATABASE_URL bypasses the safety check."""
        from src.master.db import sqlite_safety_check

        data_dir = str(tmp_path)
        _make_sqlite_with_workspaces(str(tmp_path / "master_data.db"))

        monkeypatch.setenv(
            "DATABASE_URL",
            "mysql+pymysql://root:secret@localhost:3306/master",
        )
        sqlite_safety_check(data_dir)  # must not raise
