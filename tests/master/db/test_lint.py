"""TC-07 / TC-07b: Static lint guards for the database layer.

TC-07: No `import sqlite3` outside src/master/db/.

After M0, `import sqlite3` must not appear in any .py file under src/
except:
  - src/master/db/          (the new package, including _prebaseline.py)
  - src/master/db_cli.py    (snapshot CLI internals)

TC-07b: No bare boolean literals in known-boolean column INSERT/UPDATE statements.

INSERT and UPDATE statements in src/ that target columns declared as BOOLEAN
in the schema (is_default, silent, enabled, structured_output) must not use
the bare integer literals 0 or 1.  All such call sites must bind a Python
bool via text() named parameters so that psycopg sends the correct BOOLEAN
type to Postgres.  The regression is caught via a regex scan of SQL string
literals in the source tree.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.master.db.conftest import _m0_available

# Allowlisted prefixes (relative to repo root) where sqlite3 imports are
# permitted after M0.
_ALLOWED_PREFIXES = (
    "src/master/db/",        # the new db package
    "src/master/db_cli.py",  # snapshot CLI
)

REPO_ROOT = Path(__file__).parents[3]

# Boolean columns by table.  These must never appear as bare 0/1 in SQL text.
_BOOLEAN_COLUMNS: dict[str, frozenset[str]] = {
    "messages":      frozenset({"silent"}),
    "staffs":        frozenset({"is_default"}),
    "event_actions": frozenset({"enabled", "structured_output", "silent"}),
}


def _find_sqlite3_imports(src_root: Path) -> list[str]:
    """Return a list of 'file:line' strings where `import sqlite3` appears."""
    violations = []
    for py_file in sorted(src_root.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT)
        rel_str = str(rel).replace("\\", "/")
        # Check if this file is in an allowed location.
        if any(rel_str.startswith(p) for p in _ALLOWED_PREFIXES):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlite3":
                        violations.append(f"{rel_str}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "sqlite3":
                    violations.append(f"{rel_str}:{node.lineno}")
    return violations


def _find_bare_bool_literals_in_sql(src_root: Path) -> list[str]:
    """Return 'file:line: description' for SQL string literals with bare 0/1 in boolean column positions.

    Scans all Python source files for string literals (str constants in the
    AST) that contain INSERT INTO <table> or UPDATE <table> SET patterns where
    a known BOOLEAN column is assigned the literal value 0 or 1.  Named
    :param bindings are fine (the value is a Python bool at bind time); it is
    the bare integer in the SQL text itself that is forbidden.

    Allowlisted paths (prebaseline, test fixtures) use SQLite-only paths and
    are permitted to use 0/1 internally.
    """
    _ALLOWLISTED = (
        "src/master/db/_prebaseline.py",
        "tests/",
    )

    # Pattern: col_name = 0 or col_name = 1 (as bare integers, not as :param)
    # Matches "col_name=0", "col_name = 1", "col_name=0,", etc.
    # Does NOT match "col_name=:col_name" or "col_name=True".
    _BARE_INT_PATTERN = re.compile(
        r"\b(?P<col>\w+)\s*=\s*(?P<val>[01])\b(?!\s*\d)"
    )

    violations = []
    for py_file in sorted(src_root.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT)
        rel_str = str(rel).replace("\\", "/")
        if any(rel_str.startswith(p) for p in _ALLOWLISTED):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            sql = node.value

            # Identify the table name from INSERT INTO or UPDATE
            table_match = re.search(
                r"(?i)\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE)\s+[\"`]?(\w+)[\"`]?",
                sql,
            )
            if not table_match:
                continue
            table = table_match.group(1).lower()
            bool_cols = _BOOLEAN_COLUMNS.get(table)
            if not bool_cols:
                continue

            for m in _BARE_INT_PATTERN.finditer(sql):
                if m.group("col").lower() in bool_cols:
                    violations.append(
                        f"{rel_str}:{node.lineno}: "
                        f"bare literal {m.group('val')!r} for boolean column "
                        f"'{m.group('col')}' in table '{table}' — "
                        f"use a Python bool via text() named param instead"
                    )
    return violations


@pytest.mark.skipif(
    not _m0_available(),
    reason=(
        "sqlite3 imports outside db/ are expected pre-M0; "
        "this guard is enforced once src.master.db.engine is available"
    ),
)
def test_no_sqlite3_imports_outside_db_package():
    """No `import sqlite3` in src/ outside the allowlisted paths post-M0."""
    src_root = REPO_ROOT / "src"
    violations = _find_sqlite3_imports(src_root)
    assert violations == [], (
        "Found disallowed `import sqlite3` after M0 in:\n  "
        + "\n  ".join(violations)
        + "\n\nAll sqlite3 usage must live in src/master/db/ or src/master/db_cli.py."
    )


def test_no_bare_bool_literals_in_boolean_columns():
    """No SQL string literal assigns bare 0 or 1 to a known BOOLEAN column.

    All INSERT/UPDATE statements targeting boolean columns must bind Python
    bool values through text() named params, not embed integer literals in the
    SQL text.  See docs/knowledge-base/lessons-learned.md (2026-10-25 entry).
    """
    src_root = REPO_ROOT / "src"
    violations = _find_bare_bool_literals_in_sql(src_root)
    assert violations == [], (
        "Found bare 0/1 literals in SQL strings for boolean columns:\n  "
        + "\n  ".join(violations)
        + "\n\nUse text() with named :param bindings and pass Python bool() values."
    )
