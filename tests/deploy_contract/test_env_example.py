"""
Automated contract tests for .env.example.

Covers: SC-18 through SC-21 from docs/test-plans/justfile-dotenv-deploy.md

Rules under test:
  - DEV_DOCKER_HOST, STAGING_DOCKER_HOST, REGISTRY appear as uncommented lines
  - No CD_ variables (commented or otherwise)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _lines() -> list[str]:
    return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def test_env_example_contains_dev_docker_host_uncommented() -> None:
    """SC-18: DEV_DOCKER_HOST must appear as an active (uncommented) assignment."""
    lines = _lines()
    active = [ln for ln in lines if re.match(r"^DEV_DOCKER_HOST\s*=", ln)]
    assert active, (
        ".env.example has no uncommented 'DEV_DOCKER_HOST=' line"
    )


def test_env_example_contains_staging_docker_host_uncommented() -> None:
    """SC-19: STAGING_DOCKER_HOST must appear as an active (uncommented) assignment."""
    lines = _lines()
    active = [ln for ln in lines if re.match(r"^STAGING_DOCKER_HOST\s*=", ln)]
    assert active, (
        ".env.example has no uncommented 'STAGING_DOCKER_HOST=' line"
    )


def test_env_example_contains_registry_uncommented() -> None:
    """SC-20: REGISTRY must appear as an active (uncommented) assignment."""
    lines = _lines()
    active = [ln for ln in lines if re.match(r"^REGISTRY\s*=", ln)]
    assert active, (
        ".env.example has no uncommented 'REGISTRY=' line"
    )


def test_env_example_contains_no_cd_variables() -> None:
    """SC-21: .env.example must not reference any CD_ variable (CD daemon is retired)."""
    lines = _lines()
    cd_lines = [ln for ln in lines if re.search(r"CD_", ln)]
    assert not cd_lines, (
        f".env.example contains CD_ variable references (CD daemon is retired): "
        f"{cd_lines}"
    )
