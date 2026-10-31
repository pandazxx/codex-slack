"""Shared fixtures for tests/master/db/.

These fixtures depend on the M0 public interface:
  - src.master.db.engine()
  - src.master.db.get_connection()
  - src.master.db.init_db()

All fixtures that need the new db/ package guard themselves with an
importorskip so the fast test loop is not blocked before M0 lands.
"""
from __future__ import annotations

import os
import importlib
import pytest

# ---------------------------------------------------------------------------
# Guard: detect whether the M0 db/ package is in place.
# After M0, `engine` is exported from src.master.db.
# Before M0, src.master.db is still the legacy flat module with no `engine`.
# ---------------------------------------------------------------------------

def _m0_available() -> bool:
    try:
        mod = importlib.import_module("src.master.db")
        return hasattr(mod, "engine")
    except Exception:
        return False


def _alembic_available() -> bool:
    try:
        import alembic  # noqa: F401
        return True
    except ImportError:
        return False


def _baseline_revision_exists() -> bool:
    from pathlib import Path
    root = Path(__file__).parents[3]  # repo root
    return (root / "alembic" / "versions" / "000000000001_baseline.py").exists()


def _db_sh_exists() -> bool:
    from pathlib import Path
    root = Path(__file__).parents[3]
    return (root / "scripts" / "db.sh").exists()


# ---------------------------------------------------------------------------
# Reusable skip markers (use as decorators or via pytest.mark.skipif)
# ---------------------------------------------------------------------------

SKIP_NO_M0 = pytest.mark.skipif(
    not _m0_available(),
    reason="src.master.db.engine not available — M0 db/ package not yet implemented",
)

SKIP_NO_ALEMBIC = pytest.mark.skipif(
    not _alembic_available(),
    reason="alembic not installed in this environment",
)

SKIP_NO_BASELINE = pytest.mark.skipif(
    not _baseline_revision_exists(),
    reason="alembic/versions/000000000001_baseline.py does not exist — M0 not yet implemented",
)

SKIP_NO_DB_SH = pytest.mark.skipif(
    not _db_sh_exists(),
    reason="scripts/db.sh does not exist — M0 not yet implemented",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _xdialect_fresh_db():
    """Drop every table (including alembic_version) in the cross-dialect DB before each test.

    Tests in this package call startup_db() against TEST_POSTGRES_URL / TEST_MYSQL_URL.
    startup_db() writes alembic_version and creates application tables.  A test that
    inserts a corrupt revision (e.g. test_behind_postgres_refuses) leaves behind a
    broken state that prevents every subsequent test from running upgrade.  This
    fixture guarantees a truly empty DB before each test, regardless of what the
    previous test did.
    """
    xurl = (
        os.environ.get("TEST_POSTGRES_URL", "").strip()
        or os.environ.get("TEST_MYSQL_URL", "").strip()
    )
    if not xurl:
        yield
        return
    from sqlalchemy import create_engine, text as sa_text, inspect as sa_inspect
    eng = create_engine(xurl)
    try:
        with eng.connect() as conn:
            insp = sa_inspect(conn)
            tables = insp.get_table_names()
            if tables:
                if eng.dialect.name == "postgresql":
                    quoted = ", ".join(f'"{t}"' for t in tables)
                    conn.execute(sa_text(f"DROP TABLE IF EXISTS {quoted} CASCADE"))
                else:
                    conn.execute(sa_text("SET FOREIGN_KEY_CHECKS = 0"))
                    for t in tables:
                        conn.execute(sa_text(f"DROP TABLE IF EXISTS `{t}`"))
                    conn.execute(sa_text("SET FOREIGN_KEY_CHECKS = 1"))
            conn.commit()
    finally:
        eng.dispose()
    yield


@pytest.fixture()
def legacy_sqlite_db(tmp_path):
    """A fresh SQLite DB initialised via the legacy init_db() path."""
    from src.master.db import init_db
    db_path = str(tmp_path / "legacy.db")
    init_db(db_path)
    return db_path


@pytest.fixture()
def alembic_sqlite_url(tmp_path):
    """A temporary DATABASE_URL pointing at a fresh SQLite file for Alembic."""
    db_path = tmp_path / "alembic.db"
    return f"sqlite+pysqlite:///{db_path}"


@pytest.fixture()
def override_database_url(monkeypatch, tmp_path):
    """Override DATABASE_URL for engine() tests, yielding the URL used."""
    url = f"sqlite+pysqlite:///{tmp_path / 'engine_test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    # Also clear module-level engine cache if the package exposes one.
    try:
        import src.master.db as db_mod
        if hasattr(db_mod, "_engine"):
            monkeypatch.setattr(db_mod, "_engine", None)
    except Exception:
        pass
    yield url
