"""TC-11, TC-12 (optional): Multi-dialect baseline smoke tests.

These tests run only when TEST_POSTGRES_URL or TEST_MYSQL_URL env vars are set.
They run the Alembic baseline against each dialect and verify the resulting
schema with SQLAlchemy inspect().

Purpose: scaffolding for the M1 (Postgres) and M2 (MySQL) CI matrix.
In M0 CI these are always skipped. Once M1 lands, TEST_POSTGRES_URL will be
set in CI and TC-11 will gate merge.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.master.db.conftest import SKIP_NO_ALEMBIC, SKIP_NO_BASELINE, SKIP_NO_M0

pytestmark = [SKIP_NO_M0, SKIP_NO_ALEMBIC, SKIP_NO_BASELINE]

REPO_ROOT = Path(__file__).parents[3]

EXPECTED_TABLES = [
    "workspaces", "staffs", "staff_sessions", "config", "topics",
    "sessions", "messages", "attachments", "chunks", "event_actions",
    "notes", "tasks", "staged_dispatches",
]


def _run_alembic_upgrade(database_url: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _inspect_table_names(database_url: str) -> set[str]:
    from sqlalchemy import create_engine, inspect as sa_inspect
    engine = create_engine(database_url)
    try:
        insp = sa_inspect(engine)
        return set(insp.get_table_names())
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# TC-11: Postgres baseline (skipped unless TEST_POSTGRES_URL set)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL not set; skipping Postgres baseline test",
)
class TestPostgresBaseline:
    def test_alembic_upgrade_succeeds(self):
        """alembic upgrade head against TEST_POSTGRES_URL exits 0."""
        url = os.environ["TEST_POSTGRES_URL"]
        result = _run_alembic_upgrade(url)
        assert result.returncode == 0, (
            f"alembic upgrade head failed on Postgres:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_all_tables_present(self):
        """All expected tables are present in the Postgres schema."""
        url = os.environ["TEST_POSTGRES_URL"]
        _run_alembic_upgrade(url)  # idempotent
        table_names = _inspect_table_names(url)
        missing = set(EXPECTED_TABLES) - table_names
        assert not missing, f"Missing tables in Postgres schema: {sorted(missing)}"

    def test_chunks_table_has_id_column(self):
        """chunks table has an id column (BIGSERIAL / IDENTITY on Postgres)."""
        from sqlalchemy import create_engine, inspect as sa_inspect
        url = os.environ["TEST_POSTGRES_URL"]
        _run_alembic_upgrade(url)
        engine = create_engine(url)
        try:
            insp = sa_inspect(engine)
            col_names = [c["name"] for c in insp.get_columns("chunks")]
        finally:
            engine.dispose()
        assert "id" in col_names, "chunks.id missing from Postgres schema"


# ---------------------------------------------------------------------------
# TC-12: MySQL baseline (skipped unless TEST_MYSQL_URL set)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("TEST_MYSQL_URL"),
    reason="TEST_MYSQL_URL not set; skipping MySQL baseline test",
)
class TestMysqlBaseline:
    def test_alembic_upgrade_succeeds(self):
        """alembic upgrade head against TEST_MYSQL_URL exits 0."""
        url = os.environ["TEST_MYSQL_URL"]
        result = _run_alembic_upgrade(url)
        assert result.returncode == 0, (
            f"alembic upgrade head failed on MySQL:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_all_tables_present(self):
        """All expected tables are present in the MySQL schema."""
        url = os.environ["TEST_MYSQL_URL"]
        _run_alembic_upgrade(url)  # idempotent
        table_names = _inspect_table_names(url)
        missing = set(EXPECTED_TABLES) - table_names
        assert not missing, f"Missing tables in MySQL schema: {sorted(missing)}"
