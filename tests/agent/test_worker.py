from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from src.agent import worker
from src.agent.worker import AgentInitError, WorkerSettings, run_worker, stage_preflight, stage_repo_sync, stage_workspace_prepare


def _git(args: list[str], cwd: str | None = None) -> None:
    completed = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)


def _create_local_repo(path) -> str:  # type: ignore[no-untyped-def]
    path.mkdir(parents=True)
    _git(["init", "-b", "main"], cwd=str(path))
    _git(["config", "user.name", "tester"], cwd=str(path))
    _git(["config", "user.email", "tester@example.com"], cwd=str(path))
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], cwd=str(path))
    _git(["commit", "-m", "init"], cwd=str(path))
    return str(path)


def test_stage_preflight_requires_auth(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN_FILE", raising=False)

    settings = WorkerSettings(
        workspace_path=str(tmp_path / "workspace"),
        repo_url="unused",
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    with pytest.raises(AgentInitError):
        stage_preflight(settings)


def test_stage_preflight_rejects_relative_gh_token_file(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN_FILE", "relative/path/token")

    settings = WorkerSettings(
        workspace_path=str(tmp_path / "workspace"),
        repo_url="unused",
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    with pytest.raises(AgentInitError, match="absolute path"):
        stage_preflight(settings)


def test_stage_preflight_accepts_absolute_gh_token_file(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    token_file = tmp_path / "gh.token"
    token_file.write_text("token", encoding="utf-8")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN_FILE", str(token_file))

    settings = WorkerSettings(
        workspace_path=str(tmp_path / "workspace"),
        repo_url="unused",
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    stage_preflight(settings)


def test_stage_repo_sync_clones_repo(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    src_repo = _create_local_repo(tmp_path / "src")
    monkeypatch.setenv("GH_TOKEN", "token")

    settings = WorkerSettings(
        workspace_path=str(tmp_path / "workspace"),
        repo_url=src_repo,
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    repo_dir = stage_repo_sync(settings)
    assert (repo_dir / ".git").exists()
    assert (repo_dir / "README.md").exists()


def test_run_worker_writes_error_status_when_repo_missing(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_TOKEN", "token")

    settings = WorkerSettings(
        workspace_path=str(tmp_path / "workspace"),
        repo_url="",
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    code = run_worker(settings)
    assert code == 1

    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "error"
    assert payload["stage"] == "repo_sync"


def test_run_worker_success_writes_ready_status(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    src_repo = _create_local_repo(tmp_path / "src")
    monkeypatch.setenv("GH_TOKEN", "token")

    settings = WorkerSettings(
        workspace_path=str(tmp_path / "workspace"),
        repo_url=src_repo,
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    monkeypatch.setattr(worker, "stage_ready", lambda _settings: None)

    code = run_worker(settings)
    assert code == 0

    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["stage"] == "ready"


def test_stage_workspace_prepare_sets_repo_local_git_identity(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    monkeypatch.setenv("AGENT_GIT_USER_NAME", "Agent User")
    monkeypatch.setenv("AGENT_GIT_USER_EMAIL", "agent@example.com")

    settings = WorkerSettings(
        workspace_path=str(tmp_path),
        repo_url=str(repo_path),
        repo_ref="main",
        repo_dir_name="src",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    stage_workspace_prepare(settings)

    name_result = subprocess.run(
        ["git", "config", "user.name"],
        cwd=str(repo_path),
        text=True,
        capture_output=True,
        check=False,
    )
    email_result = subprocess.run(
        ["git", "config", "user.email"],
        cwd=str(repo_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert name_result.stdout.strip() == "Agent User"
    assert email_result.stdout.strip() == "agent@example.com"


def test_load_worker_settings_uses_writable_default_status_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("AGENT_STATUS_FILE", raising=False)

    settings = worker.load_worker_settings()

    assert settings.status_file == "/tmp/master-agent/status.json"


def test_run_worker_returns_preflight_error_even_if_status_path_is_unwritable(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN_FILE", raising=False)

    settings = WorkerSettings(
        workspace_path=str(tmp_path / "workspace"),
        repo_url="unused",
        repo_ref="main",
        repo_dir_name="repo",
        status_file="/proc/1/status.json",
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    code = run_worker(settings)

    assert code == 1
