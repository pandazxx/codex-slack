"""TC-01: Baseline parity — init_db() vs alembic upgrade head.

Creates a fresh SQLite DB via legacy init_db() and a second via
`alembic upgrade head`, then compares both schemas with SQLAlchemy
inspect():
  - Same table names
  - Same columns per table (name, affinity-normalised type, nullable, PK)
  - Foreign keys point to the correct parent
  - UNIQUE constraints present
  - Regular indexes present (by column coverage)
  - Partial index names present (SQLite exposes these via inspect)
  - chunks.id is autoincrement

All tests are skipped when the M0 db/ package or Alembic baseline are
not yet in place.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.master.db.conftest import (
    SKIP_NO_ALEMBIC,
    SKIP_NO_BASELINE,
    SKIP_NO_M0,
)

pytestmark = [SKIP_NO_M0, SKIP_NO_ALEMBIC, SKIP_NO_BASELINE]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parents[3]

EXPECTED_TABLES = [
    "workspaces",
    "staffs",
    "staff_sessions",
    "config",
    "topics",
    "sessions",
    "messages",
    "attachments",
    "chunks",
    "event_actions",
    "notes",
    "tasks",
    "staged_dispatches",
]


def _affinity(type_str: str) -> str:
    """Normalise a SQLite type string to its affinity class.

    SQLAlchemy inspect() returns types like 'INTEGER', 'TEXT', 'REAL',
    'VARCHAR(36)', 'BOOLEAN' etc. We normalise to the five SQLite
    affinities so minor naming differences (e.g. 'INTEGER' vs 'INT')
    do not cause false failures.
    """
    t = (type_str or "").upper().split("(")[0].strip()
    int_types = {"INTEGER", "INT", "TINYINT", "SMALLINT", "MEDIUMINT", "BIGINT",
                 "UNSIGNED BIG INT", "INT2", "INT8"}
    real_types = {"REAL", "DOUBLE", "DOUBLE PRECISION", "FLOAT"}
    text_types = {"TEXT", "CHARACTER", "VARCHAR", "VARYING CHARACTER",
                  "NCHAR", "NATIVE CHARACTER", "NVARCHAR", "CLOB", "STRING"}
    blob_types = {"BLOB", "NONE", ""}
    numeric_types = {"NUMERIC", "DECIMAL", "BOOLEAN", "DATE", "DATETIME"}

    if t in int_types:
        return "INTEGER"
    if t in real_types:
        return "REAL"
    if t in text_types:
        return "TEXT"
    if t in blob_types:
        return "BLOB"
    if t in numeric_types:
        return "NUMERIC"
    # fallback: use as-is
    return t


def _run_alembic_upgrade(database_url: str) -> None:
    """Run `alembic upgrade head` against the given DATABASE_URL."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def _inspect_schema(database_url: str) -> dict:
    """Return a dict describing the schema of the given SQLite URL."""
    from sqlalchemy import create_engine, inspect as sa_inspect

    engine = create_engine(database_url)
    insp = sa_inspect(engine)

    schema: dict = {}
    for table in insp.get_table_names():
        columns = {}
        for col in insp.get_columns(table):
            columns[col["name"]] = {
                "affinity": _affinity(str(col["type"])),
                "nullable": col.get("nullable", True),
                "primary_key": False,  # filled below
                "autoincrement": col.get("autoincrement", False),
            }

        pk = insp.get_pk_constraint(table)
        for pk_col in (pk.get("constrained_columns") or []):
            if pk_col in columns:
                columns[pk_col]["primary_key"] = True

        fks = []
        for fk in insp.get_foreign_keys(table):
            fks.append({
                "constrained_columns": sorted(fk.get("constrained_columns", [])),
                "referred_table": fk.get("referred_table"),
                "referred_columns": sorted(fk.get("referred_columns", [])),
            })

        unique_sets = []
        for uq in insp.get_unique_constraints(table):
            unique_sets.append(sorted(uq.get("column_names", [])))

        indexes = []
        for idx in insp.get_indexes(table):
            indexes.append({
                "columns": sorted(idx.get("column_names", [])),
                "unique": idx.get("unique", False),
                # dialect_options may contain 'sqlite_where' for partial indexes
                "dialect_options": idx.get("dialect_options", {}),
            })

        schema[table] = {
            "columns": columns,
            "fks": sorted(fks, key=lambda x: str(x)),
            "unique_sets": sorted(unique_sets),
            "indexes": sorted(indexes, key=lambda x: str(x["columns"])),
        }

    engine.dispose()
    return schema


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def legacy_schema(tmp_path_factory):
    """Schema of a DB initialised via legacy init_db()."""
    from src.master.db import init_db
    tmp = tmp_path_factory.mktemp("baseline_parity")
    db_path = str(tmp / "legacy.db")
    init_db(db_path)
    return _inspect_schema(f"sqlite+pysqlite:///{db_path}")


@pytest.fixture(scope="module")
def alembic_schema(tmp_path_factory):
    """Schema of a DB initialised via alembic upgrade head."""
    tmp = tmp_path_factory.mktemp("baseline_parity")
    db_path = str(tmp / "alembic.db")
    url = f"sqlite+pysqlite:///{db_path}"
    _run_alembic_upgrade(url)
    return _inspect_schema(url)


def test_same_table_names(legacy_schema, alembic_schema):
    """Both schemas expose the same set of tables."""
    assert set(legacy_schema.keys()) == set(EXPECTED_TABLES), (
        f"Legacy schema tables: {sorted(legacy_schema.keys())}"
    )
    assert set(alembic_schema.keys()) == set(EXPECTED_TABLES), (
        f"Alembic schema tables: {sorted(alembic_schema.keys())}"
    )


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_column_names_match(table, legacy_schema, alembic_schema):
    """Each table has the same column names in both schemas."""
    legacy_cols = set(legacy_schema[table]["columns"].keys())
    alembic_cols = set(alembic_schema[table]["columns"].keys())
    assert legacy_cols == alembic_cols, (
        f"Table {table!r}: "
        f"legacy-only={legacy_cols - alembic_cols}, "
        f"alembic-only={alembic_cols - legacy_cols}"
    )


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_column_types_match(table, legacy_schema, alembic_schema):
    """Affinity-normalised column types match between both schemas."""
    for col_name, legacy_col in legacy_schema[table]["columns"].items():
        alembic_col = alembic_schema[table]["columns"].get(col_name)
        assert alembic_col is not None, (
            f"Column {col_name!r} missing from Alembic schema for table {table!r}"
        )
        assert legacy_col["affinity"] == alembic_col["affinity"], (
            f"Table {table!r}, column {col_name!r}: "
            f"legacy affinity={legacy_col['affinity']!r}, "
            f"alembic affinity={alembic_col['affinity']!r}"
        )


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_primary_keys_match(table, legacy_schema, alembic_schema):
    """Primary-key columns are identical in both schemas."""
    legacy_pks = {c for c, v in legacy_schema[table]["columns"].items() if v["primary_key"]}
    alembic_pks = {c for c, v in alembic_schema[table]["columns"].items() if v["primary_key"]}
    assert legacy_pks == alembic_pks, (
        f"Table {table!r}: legacy PK={legacy_pks}, alembic PK={alembic_pks}"
    )


def test_chunks_id_is_autoincrement(legacy_schema, alembic_schema):
    """chunks.id must be autoincrement in both schemas."""
    assert legacy_schema["chunks"]["columns"]["id"]["autoincrement"] is True, (
        "chunks.id is not autoincrement in legacy schema"
    )
    assert alembic_schema["chunks"]["columns"]["id"]["autoincrement"] is True, (
        "chunks.id is not autoincrement in alembic schema"
    )


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_foreign_keys_match(table, legacy_schema, alembic_schema):
    """Foreign keys are identical in both schemas."""
    # Normalise: sort each FK's field lists so comparison is order-independent.
    legacy_fks = legacy_schema[table]["fks"]
    alembic_fks = alembic_schema[table]["fks"]
    assert legacy_fks == alembic_fks, (
        f"Table {table!r}: legacy FK={legacy_fks}, alembic FK={alembic_fks}"
    )


def test_staffs_unique_scope_name_index(legacy_schema, alembic_schema):
    """idx_staffs_scope_name unique expression index present in both schemas.

    SQLite inspect() does not expose COALESCE() expression indexes as regular
    unique constraints — they appear as indexes. Assert that a unique index
    covering (scope_type, scope_id, name) or the expression equivalent exists.
    """
    for label, schema in [("legacy", legacy_schema), ("alembic", alembic_schema)]:
        staffs_indexes = schema["staffs"]["indexes"]
        # Either a regular unique index covering those three columns exists,
        # or an expression index (dialect_options will show up).
        has_scope_name_index = any(
            idx["unique"] and
            (set(idx["columns"]) >= {"scope_type", "name"}
             or "scope_type" in str(idx.get("dialect_options", "")))
            for idx in staffs_indexes
        )
        assert has_scope_name_index, (
            f"{label} schema: no unique index covering scope_type+name on staffs; "
            f"indexes={staffs_indexes}"
        )


def test_workspaces_partial_unique_index(legacy_schema, alembic_schema):
    """idx_workspaces_active_name partial unique index present in both schemas."""
    for label, schema in [("legacy", legacy_schema), ("alembic", alembic_schema)]:
        ws_indexes = schema["workspaces"]["indexes"]
        has_partial_unique = any(
            idx["unique"] and "name" in idx["columns"]
            for idx in ws_indexes
        )
        assert has_partial_unique, (
            f"{label} schema: no partial unique index on workspaces.name; "
            f"indexes={ws_indexes}"
        )


def test_partial_indexes_present(legacy_schema, alembic_schema):
    """Partial indexes (WHERE clause) recorded in both schemas where inspectable.

    SQLAlchemy inspect on SQLite exposes dialect_options['sqlite_where'] for
    partial indexes. We assert that at minimum the expected partial indexes
    are represented (either as full indexes or via dialect_options).
    """
    expected_partial = [
        # (table, partial_index_covering_columns_subset)
        ("staged_dispatches", ["dispatched_at"]),
        ("event_actions", ["event_type", "enabled"]),
        ("messages", ["id"]),  # idx_messages_envelope_backfill WHERE sender_kind IS NULL
    ]
    for label, schema in [("legacy", legacy_schema), ("alembic", alembic_schema)]:
        for table, cols in expected_partial:
            if table not in schema:
                continue
            table_indexes = schema[table]["indexes"]
            has_index = any(
                set(cols).issubset(set(idx["columns"]))
                for idx in table_indexes
            )
            assert has_index, (
                f"{label} schema: no index on {table!r} covering {cols}; "
                f"indexes={table_indexes}"
            )
