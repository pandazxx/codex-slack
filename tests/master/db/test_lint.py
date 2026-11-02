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


def _find_bare_sqlite3_connect_in_tests(
    tests_root: Path,
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Return 'file:line' strings for unguarded sqlite3.connect() calls in test files.

    Scans every .py file under tests_root except tests_root/master/db/.  For
    each file that imports sqlite3 (in any of the four binding forms below),
    the function checks two things:

    1. Unguarded connect calls — any call to a name bound to sqlite3.connect
       that is *not* inside a function or class decorated with
       ``@pytest.mark.sqlite_only``.

    2. Module-level sqlite3 import with no sqlite_only usage at all — a file
       that imports sqlite3 but never uses the ``sqlite_only`` marker in any
       form is almost certainly a dead import (m-4) or a missed conversion.

    Detected import binding forms
    ────────────────────────────
    - ``import sqlite3``                       → name "sqlite3" bound to module
    - ``import sqlite3 as sq``                 → name "sq" bound to module
    - ``from sqlite3 import connect``          → name "connect" bound to function
    - ``from sqlite3 import connect as c``     → name "c" bound to function
    """
    _repo_root = repo_root if repo_root is not None else REPO_ROOT
    allowed_prefix = str(tests_root / "master" / "db")

    violations: list[str] = []
    for py_file in sorted(tests_root.rglob("*.py")):
        if str(py_file).startswith(allowed_prefix):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        rel = str(py_file.relative_to(_repo_root)).replace("\\", "/")

        # Collect names that refer to the sqlite3 module or its connect function.
        module_aliases: set[str] = set()   # names bound to the sqlite3 module
        connect_aliases: set[str] = set()  # names bound to sqlite3.connect

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlite3":
                        bound_name = alias.asname if alias.asname else alias.name
                        module_aliases.add(bound_name)
            elif isinstance(node, ast.ImportFrom):
                if node.module == "sqlite3":
                    for alias in node.names:
                        if alias.name == "connect":
                            bound_name = alias.asname if alias.asname else alias.name
                            connect_aliases.add(bound_name)

        if not module_aliases and not connect_aliases:
            continue

        # Collect sqlite_only-protected ranges (function/method and class).
        sqlite_only_ranges: set[tuple[int, int]] = set()
        has_sqlite_only = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if "sqlite_only" in ast.unparse(dec):
                        sqlite_only_ranges.add((node.lineno, node.end_lineno))
                        has_sqlite_only = True
            elif isinstance(node, ast.ClassDef):
                if any("sqlite_only" in ast.unparse(d) for d in node.decorator_list):
                    sqlite_only_ranges.add((node.lineno, node.end_lineno))
                    has_sqlite_only = True

        # Flag files that import sqlite3 at module level but never use sqlite_only.
        if not has_sqlite_only:
            violations.append(
                f"{rel}:1: imports sqlite3 at module level with no sqlite_only usage"
            )
            # Still scan for connect calls — both diagnostics are useful.

        # Find calls to sqlite3.connect(...) via any binding.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_connect_call = False

            if isinstance(func, ast.Attribute):
                # module_alias.connect(...)
                if func.attr == "connect" and isinstance(func.value, ast.Name):
                    if func.value.id in module_aliases:
                        is_connect_call = True
            elif isinstance(func, ast.Name):
                # connect_alias(...)  — from sqlite3 import connect [as ...]
                if func.id in connect_aliases:
                    is_connect_call = True

            if not is_connect_call:
                continue

            call_line = node.lineno
            protected = any(start <= call_line <= end for start, end in sqlite_only_ranges)
            if not protected:
                violations.append(f"{rel}:{call_line}")

    return violations


def test_no_bare_sqlite3_connect_in_tests():
    """No test file outside tests/master/db/ may call sqlite3.connect()
    unless the enclosing function or class carries @pytest.mark.sqlite_only.

    sqlite3.connect() bypasses DATABASE_URL and makes tests silently run on
    SQLite even when the Postgres CI leg is active, hiding dialect portability
    bugs.  The right fix is to use get_connection(db_url) (where db_url comes
    from the db_url fixture in tests/master/conftest.py), which respects the
    configured backend.

    Exceptions:
    - tests/master/db/  — SQLite-specific tooling, snapshot/migration code.
    - Any test function/class decorated with @pytest.mark.sqlite_only — these
      tests only run on SQLite and the bare connect() is intentional.
    """
    tests_root = REPO_ROOT / "tests"
    violations = _find_bare_sqlite3_connect_in_tests(tests_root)
    assert violations == [], (
        f"Found {len(violations)} unguarded sqlite3.connect() call(s) in test files:\n  "
        + "\n  ".join(violations)
        + "\n\nEither:\n"
        "  (a) Replace sqlite3.connect(path) with get_connection(db_url) where db_url\n"
        "      is the db_url fixture from tests/master/conftest.py, OR\n"
        "  (b) Mark the enclosing test function/class with @pytest.mark.sqlite_only\n"
        "      (only if it specifically tests SQLite-only behaviour).\n"
        "See docs/test-plans/postgres-coverage.md for guidance."
    )


def test_sqlite3_connect_detector_catches_all_import_forms(tmp_path):
    """Self-test: _find_bare_sqlite3_connect_in_tests detects every bypass form.

    Each snippet exercises a different way of obtaining a connection to sqlite3.
    All four forms must be flagged as violations when they appear outside a
    sqlite_only-protected scope, and must be *allowed* when they appear inside one.
    """
    snippets = {
        "plain_import.py": (
            "import sqlite3\n"
            "def test_it():\n"
            "    conn = sqlite3.connect('/tmp/x.db')\n"
        ),
        "aliased_module.py": (
            "import sqlite3 as sq\n"
            "def test_it():\n"
            "    conn = sq.connect('/tmp/x.db')\n"
        ),
        "from_import.py": (
            "from sqlite3 import connect\n"
            "def test_it():\n"
            "    conn = connect('/tmp/x.db')\n"
        ),
        "from_import_aliased.py": (
            "from sqlite3 import connect as c\n"
            "def test_it():\n"
            "    conn = c('/tmp/x.db')\n"
        ),
    }
    for name, src in snippets.items():
        (tmp_path / name).write_text(src)

    violations = _find_bare_sqlite3_connect_in_tests(tmp_path, repo_root=tmp_path)
    # Each snippet produces two violations: one for the dead import and one for the call.
    call_violations = [v for v in violations if not v.endswith("no sqlite_only usage")]
    assert len(call_violations) == 4, (
        f"Expected 4 unguarded-call violations (one per snippet), got {len(call_violations)}:\n"
        + "\n".join(call_violations)
    )

    # Each snippet also triggers the dead-import diagnostic.
    import_violations = [v for v in violations if "no sqlite_only usage" in v]
    assert len(import_violations) == 4, (
        f"Expected 4 dead-import violations, got {len(import_violations)}:\n"
        + "\n".join(import_violations)
    )


def test_sqlite3_connect_detector_allows_sqlite_only_guard(tmp_path):
    """Self-test: connect calls inside sqlite_only-decorated functions are allowed."""
    snippets = {
        "guarded_function.py": (
            "import pytest\n"
            "import sqlite3\n"
            "import sqlite3 as sq\n"
            "from sqlite3 import connect\n"
            "from sqlite3 import connect as c\n"
            "@pytest.mark.sqlite_only\n"
            "def test_it():\n"
            "    sqlite3.connect('/tmp/x.db')\n"
            "    sq.connect('/tmp/x.db')\n"
            "    connect('/tmp/x.db')\n"
            "    c('/tmp/x.db')\n"
        ),
        "guarded_class.py": (
            "import pytest\n"
            "import sqlite3\n"
            "@pytest.mark.sqlite_only\n"
            "class TestSqliteOnly:\n"
            "    def test_it(self):\n"
            "        sqlite3.connect('/tmp/x.db')\n"
        ),
    }
    for name, src in snippets.items():
        (tmp_path / name).write_text(src)

    violations = _find_bare_sqlite3_connect_in_tests(tmp_path, repo_root=tmp_path)
    call_violations = [v for v in violations if not v.endswith("no sqlite_only usage")]
    assert call_violations == [], (
        f"Expected no unguarded-call violations for sqlite_only-protected code, got:\n"
        + "\n".join(call_violations)
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
