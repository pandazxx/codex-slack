"""TC-07: Lint guard — no `import sqlite3` outside src/master/db/.

After M0, `import sqlite3` must not appear in any .py file under src/
except:
  - src/master/db/          (the new package, including _prebaseline.py)
  - src/master/db_cli.py    (snapshot CLI internals)

The test walks src/ with ast.parse() and asserts zero violations.

This test is SKIPPED pre-M0 because sqlite3 is still legitimately
imported in main.py, mqtt_client.py, notify.py, dispatch.py, staffs.py,
and workspaces.py. Once the engineer confirms M0 is complete (the db/
package exists with engine()), the skip is replaced by the assertion.
"""
from __future__ import annotations

import ast
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
