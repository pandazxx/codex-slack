"""Dialect-specific SQL helpers.

These helpers paper over the places where SQLite, Postgres, and MySQL
require different syntax for the same logical operation.  Call sites that
today write raw SQLite-specific SQL change to the appropriate helper here;
cross-dialect text() SQL that already works unchanged is left as text().

Supported dialects and the driver names SQLAlchemy uses:
  sqlite      (pysqlite)
  postgresql  (psycopg)
  mysql       (pymysql)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Connection

if TYPE_CHECKING:
    # _ConnectionShim is defined in __init__.py; avoid a circular import at
    # module level by referencing it only in type annotations under TYPE_CHECKING.
    from . import _ConnectionShim


def upsert(
    conn: "_ConnectionShim",
    table: str,
    values: dict[str, Any],
    conflict_cols: list[str],
    update_cols: list[str] | None = None,
) -> None:
    """Insert a row using the dialect-appropriate upsert strategy.

    When update_cols is None the operation is "insert or ignore" — a
    pre-existing conflicting row is left unchanged.

    When update_cols is provided the listed columns are overwritten on the
    conflicting row (the values come from the incoming row).

    Dialect mapping
    ---------------
    Insert-ignore:
      SQLite / Postgres  INSERT INTO … ON CONFLICT DO NOTHING
      MySQL              INSERT IGNORE INTO …

    Upsert with update:
      SQLite / Postgres  INSERT INTO … ON CONFLICT(conflict_cols) DO UPDATE SET …
      MySQL              INSERT INTO … ON DUPLICATE KEY UPDATE …
    """
    dialect = conn._conn.dialect.name  # 'sqlite', 'postgresql', 'mysql'
    cols = list(values.keys())
    col_list = ", ".join(cols)
    val_placeholders = ", ".join(f":{c}" for c in cols)

    if update_cols is None:
        if dialect == "mysql":
            sql = f"INSERT IGNORE INTO {table} ({col_list}) VALUES ({val_placeholders})"
        else:
            # SQLite 3.24+ and all Postgres versions support ON CONFLICT DO NOTHING.
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({val_placeholders})"
                f" ON CONFLICT DO NOTHING"
            )
    else:
        if dialect == "mysql":
            update_clause = ", ".join(f"{c}=VALUES({c})" for c in update_cols)
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({val_placeholders})"
                f" ON DUPLICATE KEY UPDATE {update_clause}"
            )
        else:
            conflict_clause = ", ".join(conflict_cols)
            update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({val_placeholders})"
                f" ON CONFLICT({conflict_clause}) DO UPDATE SET {update_clause}"
            )

    conn.execute(sa_text(sql), values)


def scope_id_match(scope_id: str | None) -> tuple[str, dict[str, Any]]:
    """Return a portable SQL fragment and params for matching a nullable scope_id column.

    `scope_id IS :param` is invalid on Postgres — IS requires a literal, not a
    bound parameter.  This helper returns either `scope_id IS NULL` (when
    scope_id is None, no extra params) or `scope_id = :scope_id` (when a
    value is provided), so call sites stay free of dialect conditionals.

    Usage::

        clause, params = scope_id_match(scope_id)
        conn.execute(
            text(f"SELECT … FROM staffs WHERE scope_type=:st AND {clause} AND name=:n"),
            {"st": scope_type, "n": name, **params},
        )
    """
    if scope_id is None:
        return "scope_id IS NULL", {}
    return "scope_id = :scope_id", {"scope_id": scope_id}


def table_exists(conn: Connection, table_name: str) -> bool:
    """Return True if the named table exists in the current schema."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(conn)
    return inspector.has_table(table_name)


def json_array_contains_fragment(column: str, param: str, dialect: str) -> str:
    """Return a dialect-appropriate SQL fragment for testing membership in a JSON text array.

    Returns a self-contained boolean expression (no leading AND) that is TRUE
    when the text value bound to `param` appears in the JSON array stored in
    `column`.  Embed in a WHERE clause::

        frag = json_array_contains_fragment("tags", ":tag", dialect)
        sql = f"SELECT ... FROM notes WHERE scope_id=:sid AND {frag}"

    Dialect mapping
    ---------------
    postgresql  EXISTS (SELECT 1 FROM jsonb_array_elements_text(col::jsonb) AS _t WHERE _t = :param)
    mysql       JSON_CONTAINS(col, JSON_QUOTE(:param))
    sqlite      EXISTS (SELECT 1 FROM json_each(col) WHERE value = :param)
    """
    if dialect == "postgresql":
        return (
            f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({column}::jsonb) AS _t WHERE _t = {param})"
        )
    if dialect == "mysql":
        return f"JSON_CONTAINS({column}, JSON_QUOTE({param}))"
    return f"EXISTS (SELECT 1 FROM json_each({column}) WHERE value = {param})"


def reset_sequences_for_table(conn: Connection, table_name: str) -> None:
    """Reset identity sequences after a bulk import so subsequent inserts
    do not collide with imported rows.

    M0: no-op — snapshot import arrives in M1.
    """
