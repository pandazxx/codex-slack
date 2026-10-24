"""TC-08, TC-09, TC-10: scripts/db.sh CLI behaviour.

Tests that:
  - `scripts/db.sh --help` exits 0 and lists expected subcommands.
  - An unknown subcommand exits non-zero.
  - `scripts/db.sh migrate` against a temp SQLite URL creates the
    alembic_version table at the baseline head revision.

All tests are skipped if scripts/db.sh does not exist.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.master.db.conftest import SKIP_NO_DB_SH, _alembic_available

pytestmark = [SKIP_NO_DB_SH]

REPO_ROOT = Path(__file__).parents[3]
DB_SH = str(REPO_ROOT / "scripts" / "db.sh")

# Baseline revision prefix — the script must create alembic_version with this.
BASELINE_REV_PREFIX = "000000000001"


def _run_db_sh(*args, extra_env: dict | None = None, **kwargs):
    """Run scripts/db.sh with the given args and return CompletedProcess."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", DB_SH, *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TC-08: --help exits 0 and lists subcommands
# ---------------------------------------------------------------------------

class TestHelp:
    def test_help_exits_zero(self):
        """scripts/db.sh --help exits with code 0."""
        result = _run_db_sh("--help")
        assert result.returncode == 0, (
            f"--help exited {result.returncode}:\n{result.stderr}"
        )

    def test_help_lists_migrate(self):
        """scripts/db.sh --help output mentions 'migrate'."""
        result = _run_db_sh("--help")
        assert "migrate" in result.stdout or "migrate" in result.stderr, (
            "Expected 'migrate' in --help output"
        )

    def test_help_lists_verify_schema(self):
        """scripts/db.sh --help output mentions 'verify-schema'."""
        result = _run_db_sh("--help")
        combined = result.stdout + result.stderr
        assert "verify-schema" in combined, (
            "Expected 'verify-schema' in --help output"
        )

    def test_help_lists_snapshot_and_migrate(self):
        """scripts/db.sh --help output mentions 'snapshot-and-migrate'."""
        result = _run_db_sh("--help")
        combined = result.stdout + result.stderr
        assert "snapshot-and-migrate" in combined, (
            "Expected 'snapshot-and-migrate' in --help output"
        )


# ---------------------------------------------------------------------------
# TC-09: unknown subcommand exits non-zero
# ---------------------------------------------------------------------------

class TestUnknownSubcommand:
    def test_unknown_subcommand_exits_nonzero(self):
        """An unknown subcommand exits with a non-zero exit code."""
        result = _run_db_sh("this-subcommand-does-not-exist-9f8a3b")
        assert result.returncode != 0, (
            "Expected non-zero exit for unknown subcommand, got 0"
        )


# ---------------------------------------------------------------------------
# TC-10: migrate creates alembic_version at baseline head
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _alembic_available(),
    reason="alembic not installed; cannot verify alembic_version table",
)
class TestMigrateSubcommand:
    def test_migrate_creates_alembic_version(self, tmp_path):
        """scripts/db.sh migrate creates the alembic_version table at the baseline head."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "migrate_test.db"
        db_url = f"sqlite+pysqlite:///{db_path}"

        result = _run_db_sh(
            "migrate",
            extra_env={"DATABASE_URL": db_url},
        )
        assert result.returncode == 0, (
            f"scripts/db.sh migrate failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Verify alembic_version table was created and has the baseline revision.
        assert db_path.exists(), "Database file was not created by migrate"
        conn = _sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        finally:
            conn.close()

        assert len(rows) == 1, (
            f"Expected exactly one row in alembic_version, got {rows!r}"
        )
        version_num = rows[0][0]
        assert version_num.startswith(BASELINE_REV_PREFIX), (
            f"Expected version_num starting with {BASELINE_REV_PREFIX!r}, "
            f"got {version_num!r}"
        )
