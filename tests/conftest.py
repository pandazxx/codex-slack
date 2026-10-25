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
    """Optionally configure DATABASE_URL for the test session.

    Behaviour:
    - If DATABASE_URL is already set in the environment (e.g. set before
      running pytest), leave it unchanged.  This allows CI to run the suite
      against Postgres by setting DATABASE_URL externally.
    - If TEST_DATABASE_URL is set, use it as DATABASE_URL for the session.
      This lets you run the full suite against a specific backend without
      polluting the real DATABASE_URL variable.
    - Otherwise, leave DATABASE_URL unset so that each integration test's
      fixture (which sets MASTER_DATA_DIR via monkeypatch) determines its
      own per-test SQLite database path, preserving test isolation.

    The db-unit tests in tests/master/db/ create explicit per-test URLs or
    paths via their own fixtures and do not rely on a session DATABASE_URL.
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

    # No DATABASE_URL override — let each test manage its own.
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
