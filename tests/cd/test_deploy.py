from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from src.cd.deploy import (
    check_health,
    deploy_image,
    get_container_status,
    get_image_repo_digest,
    pull_image,
    rollback_image,
)

_DIGEST = "ghcr.io/org/image@sha256:abc123"
_OLD_DIGEST = "ghcr.io/org/image@sha256:def456"


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# get_image_repo_digest
# ---------------------------------------------------------------------------


def test_get_image_repo_digest_returns_digest_on_success() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(stdout=_DIGEST)) as mock_run:
        result = get_image_repo_digest("ghcr.io/org/image:latest")
    assert result == _DIGEST
    mock_run.assert_called_once_with(
        ["podman", "image", "inspect", "--format", "{{index .RepoDigests 0}}", "ghcr.io/org/image:latest"]
    )


def test_get_image_repo_digest_returns_none_on_failure() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(returncode=1)):
        result = get_image_repo_digest("ghcr.io/org/image:latest")
    assert result is None


def test_get_image_repo_digest_returns_none_on_empty_output() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(stdout="")):
        result = get_image_repo_digest("ghcr.io/org/image:latest")
    assert result is None


# ---------------------------------------------------------------------------
# pull_image
# ---------------------------------------------------------------------------


def test_pull_image_returns_digest_on_success() -> None:
    with patch("src.cd.deploy._run", return_value=_proc()) as mock_run, \
         patch("src.cd.deploy.get_image_repo_digest", return_value=_DIGEST):
        result = pull_image("ghcr.io/org/image:latest")
    assert result == _DIGEST
    mock_run.assert_called_once_with(["podman", "pull", "ghcr.io/org/image:latest"])


def test_pull_image_returns_none_on_pull_failure() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(returncode=1, stderr="network error")):
        result = pull_image("ghcr.io/org/image:latest")
    assert result is None


# ---------------------------------------------------------------------------
# get_container_status
# ---------------------------------------------------------------------------


def test_get_container_status_running() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(stdout="running")):
        assert get_container_status("codex-slack-master") == "running"


def test_get_container_status_returns_none_when_not_found() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(returncode=1)):
        assert get_container_status("codex-slack-master") is None


# ---------------------------------------------------------------------------
# deploy_image
# ---------------------------------------------------------------------------


def test_deploy_image_calls_compose_with_correct_args() -> None:
    with patch("src.cd.deploy._run", return_value=_proc()) as mock_run:
        ok = deploy_image(
            image_ref=_DIGEST,
            compose_binary="docker compose",
            compose_file="docker-compose.master.yml",
            compose_service="codex-slack-master",
            env_file=None,
            dry_run=False,
        )
    assert ok is True
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert "docker" in cmd and "compose" in cmd
    assert "-f" in cmd and "docker-compose.master.yml" in cmd
    assert "up" in cmd and "--force-recreate" in cmd
    assert "codex-slack-master" in cmd
    assert "--env-file" not in cmd
    assert kwargs.get("extra_env", {}).get("MASTER_RUNTIME_IMAGE") == _DIGEST


def test_deploy_image_includes_env_file_when_set() -> None:
    with patch("src.cd.deploy._run", return_value=_proc()) as mock_run:
        deploy_image(
            image_ref=_DIGEST,
            compose_binary="docker compose",
            compose_file="docker-compose.master.yml",
            compose_service="codex-slack-master",
            env_file="/path/to/.env",
            dry_run=False,
        )
    cmd = mock_run.call_args[0][0]
    assert "--env-file" in cmd
    idx = cmd.index("--env-file")
    assert cmd[idx + 1] == "/path/to/.env"


def test_deploy_image_dry_run_skips_subprocess() -> None:
    with patch("src.cd.deploy._run") as mock_run:
        ok = deploy_image(
            image_ref=_DIGEST,
            compose_binary="docker compose",
            compose_file="docker-compose.master.yml",
            compose_service="codex-slack-master",
            env_file=None,
            dry_run=True,
        )
    assert ok is True
    mock_run.assert_not_called()


def test_deploy_image_returns_false_on_compose_failure() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(returncode=1, stderr="port in use")):
        ok = deploy_image(
            image_ref=_DIGEST,
            compose_binary="docker compose",
            compose_file="docker-compose.master.yml",
            compose_service="codex-slack-master",
            env_file=None,
            dry_run=False,
        )
    assert ok is False


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


def test_check_health_returns_true_when_running() -> None:
    with patch("src.cd.deploy.time") as mock_time, \
         patch("src.cd.deploy.get_container_status", return_value="running"):
        result = check_health("codex-slack-master", delay_seconds=5)
    assert result is True
    mock_time.sleep.assert_called_once_with(5)


def test_check_health_returns_false_when_exited() -> None:
    with patch("src.cd.deploy.time"), \
         patch("src.cd.deploy.get_container_status", return_value="exited"):
        result = check_health("codex-slack-master", delay_seconds=5)
    assert result is False


def test_check_health_returns_false_when_container_gone() -> None:
    with patch("src.cd.deploy.time"), \
         patch("src.cd.deploy.get_container_status", return_value=None):
        result = check_health("codex-slack-master", delay_seconds=5)
    assert result is False


# ---------------------------------------------------------------------------
# rollback_image
# ---------------------------------------------------------------------------


def test_rollback_image_pulls_then_deploys() -> None:
    with patch("src.cd.deploy._run", return_value=_proc()) as mock_run, \
         patch("src.cd.deploy.deploy_image", return_value=True) as mock_deploy:
        ok = rollback_image(
            image_ref=_OLD_DIGEST,
            compose_binary="docker compose",
            compose_file="docker-compose.master.yml",
            compose_service="codex-slack-master",
            env_file=None,
            dry_run=False,
        )
    assert ok is True
    mock_run.assert_called_once_with(["podman", "pull", _OLD_DIGEST])
    mock_deploy.assert_called_once()


def test_rollback_image_returns_false_when_pull_fails() -> None:
    with patch("src.cd.deploy._run", return_value=_proc(returncode=1, stderr="not found")):
        ok = rollback_image(
            image_ref=_OLD_DIGEST,
            compose_binary="docker compose",
            compose_file="docker-compose.master.yml",
            compose_service="codex-slack-master",
            env_file=None,
            dry_run=False,
        )
    assert ok is False


def test_rollback_image_dry_run_skips_pull() -> None:
    with patch("src.cd.deploy._run") as mock_run, \
         patch("src.cd.deploy.deploy_image", return_value=True):
        rollback_image(
            image_ref=_OLD_DIGEST,
            compose_binary="docker compose",
            compose_file="docker-compose.master.yml",
            compose_service="codex-slack-master",
            env_file=None,
            dry_run=True,
        )
    mock_run.assert_not_called()
