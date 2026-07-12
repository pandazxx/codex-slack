"""
Automated contract tests for the justfile.

Covers: SC-22 through SC-31 from docs/test-plans/justfile-dotenv-deploy.md

Rules under test:
  - set dotenv-load := true is present
  - Each expected recipe name appears as a recipe definition line
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
JUSTFILE = REPO_ROOT / "justfile"

# Recipe names expected to appear as public (non-private) recipe definitions.
# A recipe definition line starts with the recipe name followed by optional
# args and a colon: e.g. "dev-up branch="":" or "deploy env tag:"
EXPECTED_RECIPES = [
    "dev-up",
    "dev-down",
    "deploy",
    "undeploy",
    "status",
    "logs",
    "shell",
    "test",
    "post-merge-cleanup",
]


def _text() -> str:
    return JUSTFILE.read_text(encoding="utf-8")


def test_justfile_parses_with_just() -> None:
    """SC-32: `just --list` must parse the justfile without errors.

    Regex checks below cannot catch syntax errors such as `{{ }}`
    interpolation nested inside bash `${...}` expansions, so run the real
    parser. Skipped when the `just` binary is unavailable (the CI test
    image installs it, so CI always enforces this).
    """
    if shutil.which("just") is None:
        pytest.skip("just binary not installed")
    result = subprocess.run(
        ["just", "--list", "--justfile", str(JUSTFILE)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"just --list failed:\n{result.stderr}"


def test_justfile_sets_dotenv_load() -> None:
    """SC-22: justfile must declare 'set dotenv-load := true'."""
    text = _text()
    assert "set dotenv-load := true" in text, (
        "justfile does not contain 'set dotenv-load := true'"
    )


def _recipe_pattern(name: str) -> re.Pattern[str]:
    # Matches a line that starts with the recipe name followed by a space/colon.
    # Excludes lines starting with '#' (comments) or '[private]' blocks.
    return re.compile(r"^" + re.escape(name) + r"(\s+\S|\s*:)", re.MULTILINE)


def test_justfile_has_dev_up_recipe() -> None:
    """SC-23: justfile must define a 'dev-up' recipe."""
    assert _recipe_pattern("dev-up").search(_text()), "justfile missing 'dev-up' recipe"


def test_justfile_has_dev_down_recipe() -> None:
    """SC-24: justfile must define a 'dev-down' recipe."""
    assert _recipe_pattern("dev-down").search(_text()), "justfile missing 'dev-down' recipe"


def test_justfile_has_deploy_recipe() -> None:
    """SC-25: justfile must define a 'deploy' recipe."""
    assert _recipe_pattern("deploy").search(_text()), "justfile missing 'deploy' recipe"


def test_justfile_has_undeploy_recipe() -> None:
    """SC-26: justfile must define an 'undeploy' recipe."""
    assert _recipe_pattern("undeploy").search(_text()), "justfile missing 'undeploy' recipe"


def test_justfile_has_status_recipe() -> None:
    """SC-27: justfile must define a 'status' recipe."""
    assert _recipe_pattern("status").search(_text()), "justfile missing 'status' recipe"


def test_justfile_has_logs_recipe() -> None:
    """SC-28: justfile must define a 'logs' recipe."""
    assert _recipe_pattern("logs").search(_text()), "justfile missing 'logs' recipe"


def test_justfile_has_shell_recipe() -> None:
    """SC-29: justfile must define a 'shell' recipe."""
    assert _recipe_pattern("shell").search(_text()), "justfile missing 'shell' recipe"


def test_justfile_has_test_recipe() -> None:
    """SC-30: justfile must define a 'test' recipe."""
    assert _recipe_pattern("test").search(_text()), "justfile missing 'test' recipe"


def test_justfile_has_post_merge_cleanup_recipe() -> None:
    """SC-31: justfile must define a 'post-merge-cleanup' recipe."""
    assert _recipe_pattern("post-merge-cleanup").search(_text()), (
        "justfile missing 'post-merge-cleanup' recipe"
    )
