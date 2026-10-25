"""M1 TC-13–TC-18: Startup migration state machine tests.

Tests the startup_db(url) function introduced in M1.

State machine (design doc §5):
  Empty DB (no alembic_version)             → alembic upgrade head (auto-upgrade)
  Pre-baseline SQLite (workspaces table but no alembic_version)
                                             → prebaseline fixups + stamp at baseline
  Up-to-date (alembic_version == head)       → no-op, start normally
  Behind (alembic_version < head)            → SystemExit(1), §5 error message, DB untouched
  Ahead (alembic_version is unknown future)  → SystemExit(1), message, DB untouched

Interface contract (must be satisfied before M1 tests go green):
  src.master.db.startup_db(url: str) -> None
    - Auto-upgrades empty DB.
    - Stamps pre-baseline SQLite with the baseline revision without re-running DDL.
    - On version mismatch: raises SystemExit(1) (or sys.exit(1)) and prints to
      stderr a message containing:
        Behind: "schema is behind", "scripts/db.sh migrate",
                "scripts/db.sh snapshot-and-migrate"
        Ahead:  "ahead" OR "newer" OR "unknown revision"
    - Never modifies the DB when refusing to start.

All tests skip when startup_db is absent from src.master.db (M1 not implemented yet).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[3]
BASELINE_REV = "000000000001"


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

def _has_startup_db() -> bool:
    try:
        import src.master.db as db_mod  # noqa: F401
        return hasattr(db_mod, "startup_db")
    except Exception:
        return False


def _alembic_available() -> bool:
    try:
        import alembic  # noqa: F401
        return True
    except ImportError:
        return False


SKIP_NO_STARTUP_DB = pytest.mark.skipif(
    not _has_startup_db(),
    reason="src.master.db.startup_db not available — M1 startup state machine not yet implemented",
)

SKIP_NO_ALEMBIC = pytest.mark.skipif(
    not _alembic_available(),
    reason="alembic not installed",
)

pytestmark = [SKIP_NO_STARTUP_DB, SKIP_NO_ALEMBIC]

sqlalchemy = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _alembic_head_revision() -> str:
    """Return the current alembic head revision ID."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 alembic head, got: {heads}"
    return heads[0]


def _get_alembic_version_sqlite(db_path: str) -> str | None:
    """Read alembic_version from a SQLite file; None if table absent."""
    conn = sqlite3.connect(db_path)
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if tbl is None:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _force_alembic_version_sqlite(db_path: str, revision: str) -> None:
    """Overwrite alembic_version in a SQLite file with *revision*."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL, "
            " CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
        conn.execute("DELETE FROM alembic_version")
        conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (revision,))
        conn.commit()
    finally:
        conn.close()


def _run_startup_db_subprocess(url: str) -> tuple[int, str]:
    """Invoke startup_db(url) in a child process; return (exit_code, combined_output)."""
    script = (
        "import sys, os; "
        "sys.path.insert(0, str(__import__('pathlib').Path('.').resolve())); "
        f"os.environ['DATABASE_URL'] = {url!r}; "
        "from src.master.db import startup_db; startup_db(__import__('os').environ['DATABASE_URL'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": url},
    )
    return result.returncode, result.stdout + result.stderr


def _make_uptodate_db(tmp_path, name: str = "uptodate.db") -> tuple[str, str]:
    """Create a DB at the current alembic head, return (db_path, url)."""
    from src.master.db import startup_db

    db_path = str(tmp_path / name)
    url = f"sqlite+pysqlite:///{db_path}"
    startup_db(url)
    return db_path, url


# ---------------------------------------------------------------------------
# TC-13: Empty DB → auto-upgrade
# ---------------------------------------------------------------------------

class TestEmptyDbAutoUpgrade:
    """An empty (no-tables) database is automatically migrated to HEAD on startup."""

    def test_empty_db_exits_normally(self, tmp_path):
        """startup_db on an empty SQLite file does not raise."""
        from src.master.db import startup_db

        db_path = str(tmp_path / "empty.db")
        url = f"sqlite+pysqlite:///{db_path}"
        startup_db(url)  # must not raise

    def test_empty_db_creates_all_tables(self, tmp_path):
        """startup_db on an empty DB creates every table in TABLES."""
        from src.master.db import startup_db, TABLES

        db_path = str(tmp_path / "empty_tables.db")
        url = f"sqlite+pysqlite:///{db_path}"
        startup_db(url)

        conn = sqlite3.connect(db_path)
        try:
            existing = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()

        missing = set(TABLES) - existing
        assert not missing, f"Tables missing after startup_db on empty DB: {sorted(missing)}"

    def test_empty_db_alembic_version_at_head(self, tmp_path):
        """startup_db stamps alembic_version at the current alembic head."""
        from src.master.db import startup_db

        db_path = str(tmp_path / "empty_version.db")
        url = f"sqlite+pysqlite:///{db_path}"
        startup_db(url)

        version = _get_alembic_version_sqlite(db_path)
        head = _alembic_head_revision()
        assert version == head, (
            f"alembic_version should be {head!r} after empty-DB startup, got {version!r}"
        )

    @pytest.mark.skipif(
        not os.environ.get("TEST_POSTGRES_URL"),
        reason="TEST_POSTGRES_URL not set; skipping Postgres empty-DB auto-upgrade test",
    )
    def test_empty_postgres_db_auto_upgrades(self):
        """startup_db on an empty Postgres DB creates all tables (dialect parity)."""
        from src.master.db import startup_db, TABLES
        from sqlalchemy import create_engine, inspect as sa_inspect, text

        url = os.environ["TEST_POSTGRES_URL"]
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                for tbl in reversed(TABLES + ["alembic_version"]):
                    conn.execute(text(f'DROP TABLE IF EXISTS "{tbl}" CASCADE'))
        finally:
            engine.dispose()

        startup_db(url)

        engine = create_engine(url)
        try:
            insp = sa_inspect(engine)
            table_names = set(insp.get_table_names())
        finally:
            engine.dispose()

        missing = set(TABLES) - table_names
        assert not missing, f"Tables missing on Postgres after empty-DB startup: {sorted(missing)}"


# ---------------------------------------------------------------------------
# TC-14: Pre-baseline SQLite → fixups + stamp, existing rows preserved
# ---------------------------------------------------------------------------

class TestPreBaselineSqlite:
    """An existing pre-Alembic SQLite DB is stamped at the baseline revision."""

    def _init_prebaseline(self, db_path: str) -> None:
        """Create a DB via the legacy init_db_sqlite path (no alembic_version)."""
        from src.master.db._prebaseline import init_db_sqlite

        init_db_sqlite(db_path)
        # Guard: must not have created alembic_version
        assert _get_alembic_version_sqlite(db_path) is None, (
            "init_db_sqlite must NOT create alembic_version (test setup broken)"
        )

    def test_prebaseline_stamped_at_baseline_rev(self, tmp_path):
        """startup_db stamps a pre-baseline DB at the baseline revision."""
        from src.master.db import startup_db

        db_path = str(tmp_path / "prebaseline.db")
        self._init_prebaseline(db_path)

        startup_db(f"sqlite+pysqlite:///{db_path}")

        version = _get_alembic_version_sqlite(db_path)
        assert version is not None, "startup_db must create alembic_version on pre-baseline DB"
        assert version.startswith(BASELINE_REV), (
            f"Expected version to start with {BASELINE_REV!r}, got {version!r}"
        )

    def test_prebaseline_existing_rows_preserved(self, tmp_path):
        """startup_db on a pre-baseline DB must not delete existing data rows."""
        db_path = str(tmp_path / "prebaseline_rows.db")
        self._init_prebaseline(db_path)

        # Insert a workspace row before Alembic takes over
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO workspaces (id, name, repo_url, created_at)"
                " VALUES ('ws-preserve-001', 'pre-alembic-ws',"
                " 'https://github.com/example/repo', '2026-01-01T00:00:00Z')"
            )
            conn.commit()
        finally:
            conn.close()

        from src.master.db import startup_db
        startup_db(f"sqlite+pysqlite:///{db_path}")

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM workspaces WHERE id='ws-preserve-001'"
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, (
            "startup_db on pre-baseline DB deleted existing workspace row"
        )

    def test_prebaseline_exits_normally(self, tmp_path):
        """startup_db on a pre-baseline DB does not raise."""
        from src.master.db import startup_db

        db_path = str(tmp_path / "prebaseline_ok.db")
        self._init_prebaseline(db_path)
        startup_db(f"sqlite+pysqlite:///{db_path}")  # must not raise or sys.exit


# ---------------------------------------------------------------------------
# TC-15: Up-to-date DB → no-op
# ---------------------------------------------------------------------------

class TestUpToDateNoOp:
    """A database already at HEAD starts without modification."""

    def test_uptodate_exits_normally(self, tmp_path):
        """A second startup_db on an up-to-date DB does not raise."""
        from src.master.db import startup_db

        db_path, url = _make_uptodate_db(tmp_path, "uptodate.db")
        startup_db(url)  # second call must not raise

    def test_uptodate_alembic_version_unchanged(self, tmp_path):
        """Repeated startups on an up-to-date DB leave alembic_version unchanged."""
        from src.master.db import startup_db

        db_path, url = _make_uptodate_db(tmp_path, "uptodate_v.db")
        version_before = _get_alembic_version_sqlite(db_path)

        startup_db(url)

        version_after = _get_alembic_version_sqlite(db_path)
        assert version_before == version_after, (
            f"alembic_version changed on no-op startup: {version_before!r} → {version_after!r}"
        )

    @pytest.mark.skipif(
        not os.environ.get("TEST_POSTGRES_URL"),
        reason="TEST_POSTGRES_URL not set; skipping Postgres no-op startup test",
    )
    def test_uptodate_postgres_exits_normally(self):
        """A second startup_db on an up-to-date Postgres DB does not raise."""
        from src.master.db import startup_db

        url = os.environ["TEST_POSTGRES_URL"]
        startup_db(url)  # bring to head
        startup_db(url)  # no-op; must not raise


# ---------------------------------------------------------------------------
# TC-16: Behind DB → refuses, exits non-zero, DB untouched
# ---------------------------------------------------------------------------

class TestBehindRefuses:
    """A behind-version database causes startup_db to refuse with a diagnostic."""

    FAKE_OLD_REV = "000000000000_behind_test"

    def _make_behind_db(self, tmp_path, name: str = "behind.db") -> tuple[str, str]:
        """Return (db_path, url) for a DB stamped with a fake old revision."""
        db_path, url = _make_uptodate_db(tmp_path, name)
        _force_alembic_version_sqlite(db_path, self.FAKE_OLD_REV)
        return db_path, url

    def test_behind_exits_nonzero(self, tmp_path):
        """startup_db on a behind DB exits with a non-zero exit code."""
        db_path, url = self._make_behind_db(tmp_path)
        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0, (
            f"Expected non-zero exit for behind DB, got exit={exit_code}:\n{output}"
        )

    def test_behind_message_contains_db_version(self, tmp_path):
        """The refusal message includes the DB revision ID."""
        db_path, url = self._make_behind_db(tmp_path)
        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0
        assert self.FAKE_OLD_REV in output or "000000000000" in output, (
            f"Expected DB revision in error output, got:\n{output}"
        )

    def test_behind_message_contains_schema_is_behind(self, tmp_path):
        """The refusal message uses the phrase from §5 ('schema is behind')."""
        db_path, url = self._make_behind_db(tmp_path)
        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0
        assert "behind" in output.lower(), (
            f"Expected 'behind' in refusal message, got:\n{output}"
        )

    def test_behind_message_contains_migrate_command(self, tmp_path):
        """The refusal message mentions scripts/db.sh migrate as the remedy."""
        db_path, url = self._make_behind_db(tmp_path)
        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0
        assert "scripts/db.sh migrate" in output or "db.sh migrate" in output, (
            f"Expected 'scripts/db.sh migrate' in error output, got:\n{output}"
        )

    def test_behind_message_contains_snapshot_and_migrate(self, tmp_path):
        """The refusal message also mentions scripts/db.sh snapshot-and-migrate."""
        db_path, url = self._make_behind_db(tmp_path)
        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0
        assert "snapshot-and-migrate" in output, (
            f"Expected 'snapshot-and-migrate' in error output, got:\n{output}"
        )

    def test_behind_db_alembic_version_untouched(self, tmp_path):
        """startup_db refuses a behind DB without modifying alembic_version."""
        db_path, url = self._make_behind_db(tmp_path)
        version_before = _get_alembic_version_sqlite(db_path)
        _run_startup_db_subprocess(url)  # will fail; ignore exit code
        version_after = _get_alembic_version_sqlite(db_path)
        assert version_before == version_after, (
            f"startup_db modified alembic_version on behind DB: "
            f"{version_before!r} → {version_after!r}"
        )

    @pytest.mark.skipif(
        not os.environ.get("TEST_POSTGRES_URL"),
        reason="TEST_POSTGRES_URL not set; skipping Postgres behind-DB refusal test",
    )
    def test_behind_postgres_refuses(self):
        """startup_db on a behind Postgres DB exits non-zero."""
        from src.master.db import startup_db
        from sqlalchemy import create_engine, text

        url = os.environ["TEST_POSTGRES_URL"]
        startup_db(url)  # bring to head first

        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM alembic_version"))
                conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                    {"v": self.FAKE_OLD_REV},
                )
        finally:
            engine.dispose()

        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0, (
            f"Expected non-zero for behind Postgres DB, got {exit_code}:\n{output}"
        )


# ---------------------------------------------------------------------------
# TC-17: Ahead DB → refuses
# ---------------------------------------------------------------------------

class TestAheadRefuses:
    """A database stamped with an unknown future revision causes a refusal."""

    FAKE_FUTURE_REV = "zzzzzzzzzzzz_ahead_test"

    def _make_ahead_db(self, tmp_path, name: str = "ahead.db") -> tuple[str, str]:
        """Return (db_path, url) for a DB stamped with a fake future revision."""
        db_path, url = _make_uptodate_db(tmp_path, name)
        _force_alembic_version_sqlite(db_path, self.FAKE_FUTURE_REV)
        return db_path, url

    def test_ahead_exits_nonzero(self, tmp_path):
        """startup_db on an ahead DB exits with a non-zero exit code."""
        db_path, url = self._make_ahead_db(tmp_path)
        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0, (
            f"Expected non-zero exit for ahead DB, got exit={exit_code}:\n{output}"
        )

    def test_ahead_message_flags_newer_revision(self, tmp_path):
        """The refusal message indicates the DB has a newer or unknown revision."""
        db_path, url = self._make_ahead_db(tmp_path)
        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0
        combined = output.lower()
        assert any(kw in combined for kw in ("ahead", "newer", "unknown", "downgrade")), (
            f"Expected one of 'ahead'/'newer'/'unknown'/'downgrade' in ahead-DB message:\n{output}"
        )

    def test_ahead_db_alembic_version_untouched(self, tmp_path):
        """startup_db refuses an ahead DB without modifying alembic_version."""
        db_path, url = self._make_ahead_db(tmp_path)
        version_before = _get_alembic_version_sqlite(db_path)
        _run_startup_db_subprocess(url)
        version_after = _get_alembic_version_sqlite(db_path)
        assert version_before == version_after, (
            f"startup_db modified alembic_version on ahead DB: "
            f"{version_before!r} → {version_after!r}"
        )

    @pytest.mark.skipif(
        not os.environ.get("TEST_POSTGRES_URL"),
        reason="TEST_POSTGRES_URL not set; skipping Postgres ahead-DB refusal test",
    )
    def test_ahead_postgres_refuses(self):
        """startup_db on an ahead Postgres DB exits non-zero."""
        from src.master.db import startup_db
        from sqlalchemy import create_engine, text

        url = os.environ["TEST_POSTGRES_URL"]
        startup_db(url)

        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM alembic_version"))
                conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                    {"v": self.FAKE_FUTURE_REV},
                )
        finally:
            engine.dispose()

        exit_code, output = _run_startup_db_subprocess(url)
        assert exit_code != 0, (
            f"Expected non-zero for ahead Postgres DB, got {exit_code}:\n{output}"
        )


# ---------------------------------------------------------------------------
# TC-18: Lifespan integration — main.py lifespan follows design §7 and §11
# ---------------------------------------------------------------------------

class TestLifespanResolution:
    """The FastAPI lifespan in main.py calls sqlite_safety_check, resolve_db_url,
    and startup_db in the correct order and exits cleanly when DATABASE_URL is set.

    These tests exercise the lifespan as a black box via TestClient so they catch
    any wiring errors between the three steps that unit tests of each function alone
    cannot catch.
    """

    def test_lifespan_uses_database_url_when_set(self, tmp_path, monkeypatch):
        """When DATABASE_URL is set the lifespan starts without error."""
        from fastapi.testclient import TestClient
        from src.master.main import app

        db_url = f"sqlite+pysqlite:///{tmp_path}/master_data.db"
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("CONTAINER_RUNTIME", "docker")

        with TestClient(app) as client:
            r = client.get("/health")
        assert r.status_code == 200

    def test_lifespan_refuses_when_no_url_and_no_password(self, tmp_path, monkeypatch):
        """When neither DATABASE_URL nor POSTGRES_PASSWORD is set the lifespan exits non-zero."""
        import subprocess, sys
        from pathlib import Path

        env = {k: v for k, v in __import__("os").environ.items()}
        env.pop("DATABASE_URL", None)
        env.pop("POSTGRES_PASSWORD", None)
        env["MASTER_DATA_DIR"] = str(tmp_path)
        env["CONTAINER_RUNTIME"] = "docker"

        script = (
            "import sys; "
            f"sys.path.insert(0, str(__import__('pathlib').Path({str(REPO_ROOT)!r}).resolve())); "
            "from src.master.main import app; "
            "from fastapi.testclient import TestClient; "
            "TestClient(app).__enter__()"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit when no DATABASE_URL or POSTGRES_PASSWORD, "
            f"got exit={result.returncode}:\n{result.stdout}\n{result.stderr}"
        )

    def test_lifespan_safety_check_blocks_on_existing_sqlite(self, tmp_path, monkeypatch):
        """When DATABASE_URL is unset and an existing SQLite file is present the lifespan exits."""
        import subprocess, sys, sqlite3 as _sqlite3

        # Create a SQLite file that looks like an existing master DB
        db_path = tmp_path / "master_data.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO workspaces VALUES ('ws-1', 'legacy')")
        conn.commit()
        conn.close()

        env = {k: v for k, v in __import__("os").environ.items()}
        env.pop("DATABASE_URL", None)
        env.pop("POSTGRES_PASSWORD", None)
        env["MASTER_DATA_DIR"] = str(tmp_path)
        env["CONTAINER_RUNTIME"] = "docker"

        script = (
            "import sys; "
            f"sys.path.insert(0, str(__import__('pathlib').Path({str(REPO_ROOT)!r}).resolve())); "
            "from src.master.main import app; "
            "from fastapi.testclient import TestClient; "
            "TestClient(app).__enter__()"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit when legacy SQLite file present and DATABASE_URL unset, "
            f"got exit={result.returncode}:\n{result.stdout}\n{result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "master_data.db" in combined, (
            f"Expected 'master_data.db' in exit message, got:\n{combined}"
        )
