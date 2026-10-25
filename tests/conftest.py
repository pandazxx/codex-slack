from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
