"""MySQL/MariaDB deferral guard tests (ADR-0020).

Verifies that startup_db and the db_cli commands refuse MySQL/MariaDB URLs
early, before touching the database.  Tests pass on both the sqlite and
postgres CI legs because they never require a real MySQL server.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[3]

EXPECTED_MSG_FRAGMENT = "MySQL support is deferred"
ADR_REF = "0020-mysql-support-details.md"

MYSQL_URL_WITH_PASS = "mysql+pymysql://user:secret@host:3306/db"
MARIADB_URL_WITH_PASS = "mariadb+pymysql://user:secret@host:3306/db"
MYSQL_PLAIN_URL = "mysql://user:secret@host/db"


def _run_startup_db(url: str, extra_env: dict | None = None) -> tuple[int, str]:
    """Run startup_db(url) in a child process and return (exit_code, combined_output)."""
    script = (
        "import sys, os; "
        "sys.path.insert(0, str(__import__('pathlib').Path('.').resolve())); "
        f"os.environ['DATABASE_URL'] = {url!r}; "
        "from src.master.db import startup_db; "
        "startup_db(__import__('os').environ['DATABASE_URL'])"
    )
    env = {**os.environ, "DATABASE_URL": url}
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout + result.stderr


def _run_db_cli(argv: list[str], extra_env: dict | None = None) -> tuple[int, str]:
    """Run db_cli as a subprocess and return (exit_code, combined_output)."""
    env = {k: v for k, v in os.environ.items()}
    env.pop("DATABASE_URL", None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-m", "src.master.db_cli"] + argv,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# startup_db refusal
# ---------------------------------------------------------------------------

class TestStartupDbRefusesMysql:
    """startup_db exits non-zero before touching the DB for any MySQL/MariaDB URL."""

    def test_mysql_pymysql_url_exits_nonzero(self):
        code, _ = _run_startup_db(MYSQL_URL_WITH_PASS)
        assert code != 0, "startup_db must exit non-zero for mysql+pymysql URL"

    def test_mariadb_url_exits_nonzero(self):
        code, _ = _run_startup_db(MARIADB_URL_WITH_PASS)
        assert code != 0, "startup_db must exit non-zero for mariadb+pymysql URL"

    def test_plain_mysql_url_exits_nonzero(self):
        code, _ = _run_startup_db(MYSQL_PLAIN_URL)
        assert code != 0, "startup_db must exit non-zero for plain mysql:// URL"

    def test_message_contains_deferred_text(self):
        _, output = _run_startup_db(MYSQL_URL_WITH_PASS)
        assert EXPECTED_MSG_FRAGMENT in output, (
            f"Expected {EXPECTED_MSG_FRAGMENT!r} in output, got:\n{output}"
        )

    def test_message_references_adr_document(self):
        _, output = _run_startup_db(MYSQL_URL_WITH_PASS)
        assert ADR_REF in output, (
            f"Expected ADR reference {ADR_REF!r} in output, got:\n{output}"
        )

    def test_password_not_in_output_mysql(self):
        _, output = _run_startup_db(MYSQL_URL_WITH_PASS)
        assert "secret" not in output, (
            f"Password must not appear in output, got:\n{output}"
        )

    def test_password_not_in_output_mariadb(self):
        _, output = _run_startup_db(MARIADB_URL_WITH_PASS)
        assert "secret" not in output, (
            f"Password must not appear in output for mariadb URL, got:\n{output}"
        )

    def test_sqlite_url_not_blocked(self, tmp_path):
        """Guard returns without error for SQLite URLs."""
        from src.master.db import _refuse_mysql
        _refuse_mysql(f"sqlite+pysqlite:///{tmp_path}/test.db")

    def test_postgres_url_not_blocked(self):
        """Guard returns without error for PostgreSQL URLs."""
        from src.master.db import _refuse_mysql
        _refuse_mysql("postgresql+psycopg://user:pass@localhost/db")

    def test_empty_url_not_blocked(self):
        """Guard returns without error for an empty string."""
        from src.master.db import _refuse_mysql
        _refuse_mysql("")


# ---------------------------------------------------------------------------
# CLI refusal
# ---------------------------------------------------------------------------

class TestCliRefusesMysql:
    """db_cli subcommands exit non-zero for MySQL source or target URLs."""

    def test_snapshot_export_mysql_source_exits_nonzero(self, tmp_path):
        code, _ = _run_db_cli([
            "snapshot", "export",
            "--source-url", MYSQL_URL_WITH_PASS,
            "--out", str(tmp_path / "snap"),
        ])
        assert code != 0, "snapshot export with MySQL source must exit non-zero"

    def test_snapshot_export_mysql_source_message(self, tmp_path):
        _, output = _run_db_cli([
            "snapshot", "export",
            "--source-url", MYSQL_URL_WITH_PASS,
            "--out", str(tmp_path / "snap"),
        ])
        assert EXPECTED_MSG_FRAGMENT in output, (
            f"Expected deferral message in snapshot export output, got:\n{output}"
        )

    def test_snapshot_export_password_not_in_output(self, tmp_path):
        _, output = _run_db_cli([
            "snapshot", "export",
            "--source-url", MYSQL_URL_WITH_PASS,
            "--out", str(tmp_path / "snap"),
        ])
        assert "secret" not in output, (
            f"Password must not appear in snapshot export output, got:\n{output}"
        )

    def test_snapshot_import_mysql_target_exits_nonzero(self, tmp_path):
        code, _ = _run_db_cli([
            "snapshot", "import",
            "--in", str(tmp_path),
            "--target-url", MYSQL_URL_WITH_PASS,
        ])
        assert code != 0, "snapshot import with MySQL target must exit non-zero"

    def test_snapshot_import_mysql_target_message(self, tmp_path):
        _, output = _run_db_cli([
            "snapshot", "import",
            "--in", str(tmp_path),
            "--target-url", MYSQL_URL_WITH_PASS,
        ])
        assert EXPECTED_MSG_FRAGMENT in output, (
            f"Expected deferral message in snapshot import output, got:\n{output}"
        )

    def test_snapshot_and_migrate_mysql_source_exits_nonzero(self):
        code, _ = _run_db_cli(
            ["snapshot-and-migrate", "--source-url", MYSQL_URL_WITH_PASS],
        )
        assert code != 0, "snapshot-and-migrate with MySQL source must exit non-zero"

    def test_snapshot_and_migrate_mysql_target_exits_nonzero(self, tmp_path):
        sqlite_url = f"sqlite+pysqlite:///{tmp_path}/src.db"
        code, _ = _run_db_cli([
            "snapshot-and-migrate",
            "--source-url", sqlite_url,
            "--target-url", MYSQL_URL_WITH_PASS,
        ])
        assert code != 0, "snapshot-and-migrate with MySQL target must exit non-zero"


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------

class TestEscapeHatch:
    """ALLOW_EXPERIMENTAL_MYSQL=1 bypasses the guard."""

    def test_helper_bypasses_when_env_set(self, monkeypatch):
        """_refuse_mysql does not call sys.exit when ALLOW_EXPERIMENTAL_MYSQL=1."""
        monkeypatch.setenv("ALLOW_EXPERIMENTAL_MYSQL", "1")
        from src.master.db import _refuse_mysql
        _refuse_mysql(MYSQL_URL_WITH_PASS)  # must return without raising SystemExit

    def test_helper_bypasses_for_mariadb_when_env_set(self, monkeypatch):
        """_refuse_mysql does not call sys.exit for MariaDB when ALLOW_EXPERIMENTAL_MYSQL=1."""
        monkeypatch.setenv("ALLOW_EXPERIMENTAL_MYSQL", "1")
        from src.master.db import _refuse_mysql
        _refuse_mysql(MARIADB_URL_WITH_PASS)

    def test_startup_db_passes_guard_with_escape_hatch(self):
        """startup_db with ALLOW_EXPERIMENTAL_MYSQL=1 does not print the deferral message."""
        code, output = _run_startup_db(
            MYSQL_URL_WITH_PASS,
            extra_env={
                "ALLOW_EXPERIMENTAL_MYSQL": "1",
                "DB_CONNECT_TIMEOUT_SECONDS": "2",
            },
        )
        assert EXPECTED_MSG_FRAGMENT not in output, (
            f"Deferral message must not appear when ALLOW_EXPERIMENTAL_MYSQL=1, "
            f"got:\n{output}"
        )
