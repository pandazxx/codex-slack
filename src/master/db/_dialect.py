"""Dialect-specific SQL helpers.

M0 scope: stubs that will be fleshed out in M1 when Postgres becomes the
default.  The SQLite implementations here are sufficient for the M0 test
suite to pass; the function signatures are the contracts that M1 will fill in.
"""
from __future__ import annotations

from sqlalchemy.engine import Connection


def upsert(
    conn: Connection,
    table: str,
    values: dict,
    conflict_cols: list[str],
    update_cols: list[str] | None = None,
) -> None:
    """Insert or update a row using the dialect-appropriate upsert.

    M0: delegated to the call site via ON CONFLICT syntax in text() SQL.
    This stub is here so imports work in M1 before the body is filled.
    """
    raise NotImplementedError("upsert() dialect helper is implemented in M1")


def table_exists(conn: Connection, table_name: str) -> bool:
    """Return True if the named table exists in the current schema."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(conn)
    return inspector.has_table(table_name)


def reset_sequences_for_table(conn: Connection, table_name: str) -> None:
    """Reset identity sequences after a bulk import so subsequent inserts
    do not collide with imported rows.

    M0: no-op — snapshot import arrives in M1.
    """
