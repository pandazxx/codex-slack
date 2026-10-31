"""TC-05, TC-06: DATABASE_URL resolution and engine() behaviour.

Tests that:
  - engine() returns a SQLAlchemy Engine.
  - With the M1 default (Postgres), engine() uses the postgresql dialect when
    POSTGRES_PASSWORD is set and DATABASE_URL is unset.
  - When neither DATABASE_URL nor POSTGRES_PASSWORD is set, engine() raises
    ValueError (resolve_db_url has no URL to return).
  - When DATABASE_URL is set, engine() reflects the override.

All tests are skipped if the M0 db/ package (engine function) is not yet
available.
"""
from __future__ import annotations

import os
import pytest

from tests.master.db.conftest import SKIP_NO_M0

pytestmark = [SKIP_NO_M0]

sqlalchemy = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")


# ---------------------------------------------------------------------------
# TC-05: M1 Postgres default and "no config" error
# ---------------------------------------------------------------------------

class TestDefaultUrl:
    def test_default_engine_is_postgres_when_password_set(self, monkeypatch, tmp_path):
        """With DATABASE_URL unset and POSTGRES_PASSWORD set, the M1 default uses postgresql."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_PASSWORD", "test-secret")

        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        eng = db_mod.engine()
        assert eng.dialect.name == "postgresql", (
            f"Expected dialect 'postgresql' for M1 Postgres default, got {eng.dialect.name!r}"
        )
        eng.dispose()

    def test_default_engine_raises_when_no_config(self, monkeypatch, tmp_path):
        """When neither DATABASE_URL nor POSTGRES_PASSWORD is set, engine() raises ValueError."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

        import importlib
        import src.master.db as db_mod
        importlib.reload(db_mod)

        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            db_mod.engine()


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
