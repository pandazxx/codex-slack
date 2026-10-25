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


def _split_values_row(sql: str, body_start: int) -> list[tuple[int, int]]:
    """Return (start, end) char-index spans for each comma-separated token in one VALUES row.

    body_start is the index immediately after the opening '(' of the row.
    Single-quoted string literals are skipped so that commas inside strings
    are not treated as separators.  Returns an empty list if the row is not
    terminated by a matching ')'.
    """
    spans: list[tuple[int, int]] = []
    depth = 1
    tok_start = body_start
    i = body_start
    while i < len(sql):
        c = sql[i]
        if c == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
        elif c == "(":
            depth += 1
            i += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                spans.append((tok_start, i))
                break
            i += 1
        elif c == "," and depth == 1:
            spans.append((tok_start, i))
            tok_start = i + 1
            i += 1
        else:
            i += 1
    return spans


def _check_insert_values_positions(
    sql: str,
    table: str,
    col_names: list[str],
    bool_positions: frozenset[int],
    rel_str: str,
    lineno: int,
) -> list[str]:
    """Return violations where a VALUES row has a bare 0/1 at a boolean column position.

    Handles both single-row and multi-row VALUES clauses.  Each row '(...)' is
    parsed independently; a bare integer token at a bool-column position is a
    violation regardless of which row it appears in.
    """
    vm = re.search(r"(?i)\bVALUES\s*", sql)
    if not vm:
        return []

    violations: list[str] = []
    i = vm.end()
    while i < len(sql):
        while i < len(sql) and sql[i] in " \t\n\r":
            i += 1
        if i >= len(sql) or sql[i] != "(":
            break
        row_start = i + 1
        spans = _split_values_row(sql, row_start)
        if spans:
            row_end = spans[-1][1] + 1  # index after closing ')'
            i = row_end
        else:
            break

        for pos, (start, end) in enumerate(spans):
            if pos not in bool_positions:
                continue
            token = sql[start:end].strip()
            if token in ("0", "1"):
                col = col_names[pos] if pos < len(col_names) else "?"
                violations.append(
                    f"{rel_str}:{lineno}: "
                    f"bare literal {token!r} at VALUES position {pos} "
                    f"for boolean column '{col}' in table '{table}' — "
                    f"use a ? placeholder with a Python bool instead"
                )

        while i < len(sql) and sql[i] in " \t\n\r":
            i += 1
        if i < len(sql) and sql[i] == ",":
            i += 1
        else:
            break
    return violations


def _find_bare_bool_literals_in_sql(roots: list[Path]) -> list[str]:
    """Return 'file:line: description' for SQL string literals with bare 0/1 in boolean column positions.

    Scans all Python source files under each root for string literals that
    contain INSERT INTO <table> (...) VALUES (...) or UPDATE <table> SET
    patterns where a known BOOLEAN column is assigned the literal integer 0 or
    1.  Named :param bindings and Python bool literals in bound-parameter tuples
    are fine — it is only the bare integer embedded in the SQL text itself that
    is forbidden.

    Only src/master/db/_prebaseline.py is allowlisted: it is SQLite-only
    migration code that cannot use text() named params.
    """
    _ALLOWLISTED = (
        "src/master/db/_prebaseline.py",
    )

    # Pattern for UPDATE SET and WHERE: col_name = 0 or col_name = 1
    # Does NOT match "col_name=:col_name" or "col_name=True".
    _BARE_SET_PATTERN = re.compile(
        r"\b(?P<col>\w+)\s*=\s*(?P<val>[01])\b(?!\s*\d)"
    )

    violations: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        for py_file in sorted(root.rglob("*.py")):
            if py_file in seen:
                continue
            seen.add(py_file)

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

                # UPDATE SET and WHERE col = 0/1
                for m in _BARE_SET_PATTERN.finditer(sql):
                    if m.group("col").lower() in bool_cols:
                        violations.append(
                            f"{rel_str}:{node.lineno}: "
                            f"bare literal {m.group('val')!r} for boolean column "
                            f"'{m.group('col')}' in table '{table}' — "
                            f"use a Python bool via text() named param instead"
                        )

                # INSERT VALUES positions
                col_match = re.match(
                    r"(?i)\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\w+\s*\(([^)]+)\)",
                    sql,
                )
                if col_match:
                    col_names = [c.strip().strip('"').lower() for c in col_match.group(1).split(",")]
                    bool_positions = frozenset(
                        i for i, col in enumerate(col_names) if col in bool_cols
                    )
                    if bool_positions:
                        violations.extend(
                            _check_insert_values_positions(
                                sql, table, col_names, bool_positions, rel_str, node.lineno
                            )
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

    Scans both src/ and tests/.  All INSERT VALUES positions and UPDATE SET /
    WHERE comparisons for known boolean columns must use bound Python bool
    values — no bare integer literals in SQL text.  The runtime rewriter
    _fix_insert_bool_literals was removed (2026-10-25); this guard is the
    enforcement mechanism going forward.  See docs/knowledge-base/lessons-learned.md.
    """
    roots = [REPO_ROOT / "src", REPO_ROOT / "tests"]
    violations = _find_bare_bool_literals_in_sql(roots)
    assert violations == [], (
        "Found bare 0/1 literals in SQL strings for boolean columns:\n  "
        + "\n  ".join(violations)
        + "\n\nUse ? placeholders with Python bool() values (for raw SQL) or"
        " text() with named :param bindings (for SQLAlchemy text())."
    )
