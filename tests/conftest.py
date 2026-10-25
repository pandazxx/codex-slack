from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "sqlite_only: skip this test when DATABASE_URL points to a non-SQLite backend",
    )


@pytest.fixture(scope="session", autouse=True)
def _non_sqlite_schema_setup():
    """Run startup_db once per session so non-SQLite schemas exist before any test runs.

    On SQLite the per-test file URL already handles fresh-DB creation, so this
    fixture is a no-op for SQLite.  On Postgres/MySQL, DATABASE_URL is set in
    the outer environment by test-db.sh; the schema must exist before the first
    function-scoped truncation fixture fires.
    """
    url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url or url.startswith("sqlite"):
        yield
        return
    from src.master.db import startup_db, _reset_engine_cache
    _reset_engine_cache()
    try:
        startup_db(url)
    except SystemExit as exc:
        pytest.exit(f"startup_db({url!r}) exited: {exc}", returncode=int(str(exc.code) or "1"))
    yield


@pytest.fixture(autouse=True)
def _skip_sqlite_only_on_non_sqlite(request):
    """Skip tests marked sqlite_only when DATABASE_URL points to a non-SQLite backend."""
    if request.node.get_closest_marker("sqlite_only") is None:
        yield
        return
    url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("TEST_DATABASE_URL", "").strip()
    if url and not url.startswith("sqlite"):
        pytest.skip(f"sqlite_only: skipped on {url.split('://')[0]} backend")
    yield


@pytest.fixture(autouse=True)
def _non_sqlite_truncate():
    """TRUNCATE all application tables before each test on non-SQLite backends.

    Rows from one test must not pollute the next test's unique-index checks.
    SQLite tests skip this — each gets its own per-test DB file via
    _set_test_database_url, so isolation is already guaranteed.
    """
    url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url or url.startswith("sqlite"):
        yield
        return
    from src.master.db import TABLES, engine as db_engine
    from sqlalchemy import text as sa_text
    eng = db_engine(url)
    dialect = eng.dialect.name
    table_list = ", ".join(TABLES)
    with eng.connect() as conn:
        if dialect == "postgresql":
            conn.execute(sa_text("SET lock_timeout = '5s'"))
            conn.execute(sa_text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))
        elif dialect == "mysql":
            conn.execute(sa_text("SET FOREIGN_KEY_CHECKS = 0"))
            for table in TABLES:
                conn.execute(sa_text(f"TRUNCATE TABLE {table}"))
            conn.execute(sa_text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()
    yield


@pytest.fixture(autouse=True)
def _set_test_database_url(monkeypatch, tmp_path):
    """Set DATABASE_URL for each test so the app and db fixtures always have a URL.

    Resolution order (mirrors the production lifespan in src/master/main.py):

    1. If DATABASE_URL is already set in the environment (e.g. set before
       running pytest, as in CI against Postgres), leave it unchanged.
    2. If TEST_DATABASE_URL is set, use it as DATABASE_URL.  This lets the
       caller run the full suite against a specific backend without polluting
       the real DATABASE_URL variable.
    3. Otherwise, set DATABASE_URL to a per-test SQLite file inside tmp_path.
       This gives every test full isolation without requiring POSTGRES_PASSWORD
       or a live Postgres instance.

    Tests that need DATABASE_URL unset (e.g. tests/master/db/test_engine.py
    and test_regression.py that probe resolve_db_url/engine default behaviour)
    use monkeypatch.delenv("DATABASE_URL", raising=False) inside the test body,
    which takes effect for that test only and is restored automatically.
    """
    if "DATABASE_URL" in os.environ:
        yield
        return

    override = os.environ.get("TEST_DATABASE_URL", "")
    if override:
        monkeypatch.setenv("DATABASE_URL", override)
        yield
        return

    url = f"sqlite+pysqlite:///{tmp_path}/master_data.db"
    monkeypatch.setenv("DATABASE_URL", url)
    yield


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Ensure a current asyncio event loop exists for every test.

    Python 3.10+ no longer auto-creates an event loop on get_event_loop().
    asyncio.run() (used in test_ws_hub) closes and clears the loop after each
    call, which breaks any subsequent test that calls asyncio.get_event_loop()
    directly (e.g. the _run helper in test_master_replay).  This fixture
    creates a fresh loop and sets it as the current loop before each test,
    then closes it after.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()
    asyncio.set_event_loop(None)
