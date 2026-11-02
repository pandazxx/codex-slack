from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_mqtt_client(monkeypatch):
    """Prevent the real paho-mqtt thread from starting during master tests.

    Without this, every TestClient(app) startup spawns a paho-mqtt network
    thread that tries to connect to mosquitto:1883 (absent in CI).  paho-mqtt's
    _reconnect_wait() sleeps in 1-second chunks and loop_stop() cannot interrupt
    that sleep — it just sets a flag and blocks on thread.join().  The result is
    ~1 s of real-clock teardown per test, adding 100+ seconds to the suite.

    Tests that need to assert on publish() calls already patch build_mqtt_client
    themselves inside the test body; this fixture's mock is overridden by those
    inner patches for the duration of the with-block, so those assertions are
    unaffected.
    """
    mock = MagicMock()
    monkeypatch.setattr("src.master.main.build_mqtt_client", lambda *a, **kw: mock)
    return mock


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    """Return the configured database URL for the current test.

    Resolution:
    - On non-SQLite backends (Postgres CI): DATABASE_URL is already set in the
      environment and the schema already exists (from _non_sqlite_schema_setup in
      the root conftest).  The _non_sqlite_truncate fixture has already cleaned
      all tables before this fixture body runs.  Returns DATABASE_URL unchanged.
    - On SQLite (default / local runs): creates a fresh per-test SQLite file at
      tmp_path/test_master.db, initialises the schema via init_db(), sets
      DATABASE_URL to the sqlite+pysqlite:// URL, and returns it.  This gives
      every test its own isolated DB without requiring a live Postgres instance.

    Tests that used to create their own SQLite file and call _init_minimal_db()
    should instead accept ``db_url`` as a fixture parameter and use
    ``get_connection(db_url)`` for raw-SQL seeding/reading.
    """
    existing = os.environ.get("DATABASE_URL", "").strip()
    if existing and not existing.startswith("sqlite"):
        # Non-SQLite backend already set up by _non_sqlite_schema_setup.
        yield existing
        return

    # SQLite path: create an initialised per-test DB.
    from src.master.db import init_db
    path = str(tmp_path / "test_master.db")
    init_db(path)
    url = f"sqlite+pysqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    yield url


@pytest.fixture()
def db_conn(db_url):
    """Yield an open _ConnectionShim to the configured backend.

    Callers use it exactly like a sqlite3.Connection (execute, commit, close)
    but the underlying DBAPI is whatever DATABASE_URL resolves to.  Close the
    connection yourself if you need to open a second connection in the same
    test; the fixture closes any unclosed connection on teardown.
    """
    from src.master.db import get_connection
    conn = get_connection(db_url)
    yield conn
    try:
        conn.close()
    except Exception:
        pass
