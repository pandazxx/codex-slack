from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True, scope="session")
def _set_test_database_url(tmp_path_factory):
    """Set DATABASE_URL to a temp SQLite file for the entire test session.

    This keeps the full test suite running on SQLite by default.  Override
    by setting TEST_DATABASE_URL before running pytest — all tests will then
    use that URL instead (e.g. TEST_DATABASE_URL=postgresql+psycopg://... for
    a Postgres run).

    Individual tests that monkeypatch DATABASE_URL are unaffected because
    monkeypatch operates at test scope and restores the value on teardown.
    """
    if "DATABASE_URL" in os.environ:
        yield
        return

    override = os.environ.get("TEST_DATABASE_URL", "")
    if override:
        os.environ["DATABASE_URL"] = override
        yield
        del os.environ["DATABASE_URL"]
        return

    db = tmp_path_factory.mktemp("session_db") / "test_session.db"
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{db}"
    yield
    del os.environ["DATABASE_URL"]


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
