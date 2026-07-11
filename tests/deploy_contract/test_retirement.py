"""
Automated retirement contract tests.

Covers: RT-01 through RT-09 from docs/test-plans/justfile-dotenv-deploy.md

Rules under test:
  - Retired artifacts (src/cd, Dockerfile.cd-daemon, scripts/gen-env.sh,
    docker-compose.staging.yml, docker-compose.override.yml,
    .sre/operations/staging-up.md) must NOT exist.
  - New runbook files (.sre/operations/deploy.md, undeploy.md) MUST exist.
  - .github/workflows/build-push.yml must not contain a 'build-cd-daemon' job.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_src_cd_directory_does_not_exist() -> None:
    """RT-01: src/cd/ directory must not exist (CD daemon retired)."""
    path = REPO_ROOT / "src" / "cd"
    assert not path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' still exists — CD daemon must be fully removed"
    )


def test_dockerfile_cd_daemon_does_not_exist() -> None:
    """RT-02: Dockerfile.cd-daemon must not exist."""
    path = REPO_ROOT / "Dockerfile.cd-daemon"
    assert not path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' still exists — retire the CD daemon Dockerfile"
    )


def test_scripts_gen_env_does_not_exist() -> None:
    """RT-03: scripts/gen-env.sh must not exist (replaced by justfile recipes)."""
    path = REPO_ROOT / "scripts" / "gen-env.sh"
    assert not path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' still exists — gen-env.sh must be removed"
    )


def test_docker_compose_staging_does_not_exist() -> None:
    """RT-04: docker-compose.staging.yml must not exist (multi-version staging shape retired)."""
    path = REPO_ROOT / "docker-compose.staging.yml"
    assert not path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' still exists — multi-version staging overlay retired"
    )


def test_docker_compose_override_does_not_exist() -> None:
    """RT-05: docker-compose.override.yml must not exist (renamed to docker-compose.dev.yml)."""
    path = REPO_ROOT / "docker-compose.override.yml"
    assert not path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' still exists — "
        "file was renamed to docker-compose.dev.yml to prevent implicit auto-merge"
    )


def test_sre_operations_staging_up_does_not_exist() -> None:
    """RT-06: .sre/operations/staging-up.md must not exist (replaced by deploy.md)."""
    path = REPO_ROOT / ".sre" / "operations" / "staging-up.md"
    assert not path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' still exists — "
        "staging-up.md is retired and replaced by deploy.md"
    )


def test_sre_operations_deploy_md_exists() -> None:
    """RT-07: .sre/operations/deploy.md must exist (new runbook for just deploy)."""
    path = REPO_ROOT / ".sre" / "operations" / "deploy.md"
    assert path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' is missing — "
        "deploy.md runbook is required for the new 'just deploy' operation"
    )


def test_sre_operations_undeploy_md_exists() -> None:
    """RT-08: .sre/operations/undeploy.md must exist (new runbook for just undeploy)."""
    path = REPO_ROOT / ".sre" / "operations" / "undeploy.md"
    assert path.exists(), (
        f"'{path.relative_to(REPO_ROOT)}' is missing — "
        "undeploy.md runbook is required for the new 'just undeploy' operation"
    )


def test_build_push_workflow_has_no_build_cd_daemon_job() -> None:
    """RT-09: .github/workflows/build-push.yml must not reference build-cd-daemon."""
    path = REPO_ROOT / ".github" / "workflows" / "build-push.yml"
    assert path.exists(), (
        "build-push.yml not found — cannot verify CD daemon job removal"
    )
    content = path.read_text(encoding="utf-8")
    assert "build-cd-daemon" not in content, (
        "build-push.yml still references 'build-cd-daemon' — "
        "remove the CD daemon CI job"
    )
