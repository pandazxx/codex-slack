"""TC-05, TC-06: DATABASE_URL resolution and engine() behaviour.

Tests that:
  - engine() returns a SQLAlchemy Engine.
  - With the M0 default (SQLite), the dialect is 'sqlite'.
  - When DATABASE_URL is set, engine() reflects the override.

All tests are skipped if the M0 db/ package (engine function) is not yet
available.

Note on the M0 default: the design doc (§12) states "SQLite is still the
default DATABASE_URL" in Phase 0. Phase 1 switches the default to Postgres.
These tests assume the M0 default is SQLite. When M1 lands the default-URL
test must be updated to expect 'postgresql'.
"""
from __future__ import annotations

import os
import pytest

from tests.master.db.conftest import SKIP_NO_M0

pytestmark = [SKIP_NO_M0]

sqlalchemy = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")


# ---------------------------------------------------------------------------
# TC-05: default SQLite dialect in M0
# ---------------------------------------------------------------------------

class TestDefaultUrl:
    def test_default_engine_is_sqlite(self, monkeypatch, tmp_path):
        """With DATABASE_URL unset, the M0 default engine uses the sqlite dialect."""
        # Ensure DATABASE_URL is not set.
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # The M0 default may use MASTER_DATA_DIR; point it at tmp_path.
        monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))

        # Clear any cached engine — the package may cache the engine at module
        # level. We reload the module to ensure a fresh engine is created.
        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        eng = db_mod.engine()
        assert eng.dialect.name == "sqlite", (
            f"Expected dialect 'sqlite' for M0 default URL, got {eng.dialect.name!r}"
        )
        eng.dispose()


# ---------------------------------------------------------------------------
# TC-06: DATABASE_URL override
# ---------------------------------------------------------------------------

class TestUrlOverride:
    def test_override_url_is_reflected_in_engine(self, monkeypatch, tmp_path):
        """When DATABASE_URL is set, engine() URL matches the override."""
        db_path = tmp_path / "override.db"
        override_url = f"sqlite+pysqlite:///{db_path}"
        monkeypatch.setenv("DATABASE_URL", override_url)

        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        eng = db_mod.engine()
        # The engine URL should reflect the override (or be equivalent).
        engine_url = str(eng.url)
        assert "sqlite" in engine_url, (
            f"Expected sqlite in engine URL after override, got {engine_url!r}"
        )
        eng.dispose()

    def test_override_url_engine_connects(self, monkeypatch, tmp_path):
        """engine() built from an override URL can connect and execute a query."""
        from sqlalchemy import text

        db_path = tmp_path / "connect_test.db"
        override_url = f"sqlite+pysqlite:///{db_path}"
        monkeypatch.setenv("DATABASE_URL", override_url)

        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        eng = db_mod.engine()
        with eng.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.fetchone() is not None
        eng.dispose()
