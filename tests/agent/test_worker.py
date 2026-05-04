from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from src.agent import worker
from src.agent.worker import AgentInitError, WorkerSettings, run_worker, stage_preflight, stage_repo_sync, stage_workspace_prepare


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return completed


def _create_local_repo(path: Path, *, readme_text: str = "hello\n") -> str:
    path.mkdir(parents=True)
    _git(["init", "-b", "main"], cwd=str(path))
    _git(["config", "user.name", "tester"], cwd=str(path))
    _git(["config", "user.email", "tester@example.com"], cwd=str(path))
    (path / "README.md").write_text(readme_text, encoding="utf-8")
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
    assert (repo_dir / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_stage_repo_sync_leaves_existing_valid_repo_unchanged(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    remote_repo = _create_local_repo(tmp_path / "remote", readme_text="remote\n")
    workspace = tmp_path / "workspace"
    existing_repo = Path(_create_local_repo(workspace / "repo", readme_text="existing\n"))
    monkeypatch.setenv("GH_TOKEN", "token")

    settings = WorkerSettings(
        workspace_path=str(workspace),
        repo_url=remote_repo,
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    repo_dir = stage_repo_sync(settings)

    assert repo_dir == existing_repo
    assert (repo_dir / "README.md").read_text(encoding="utf-8") == "existing\n"


def test_stage_repo_sync_rejects_existing_non_git_repo_path(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    src_repo = _create_local_repo(tmp_path / "src")
    workspace = tmp_path / "workspace"
    bad_repo = workspace / "repo"
    bad_repo.mkdir(parents=True)
    (bad_repo / "README.md").write_text("not a git repo\n", encoding="utf-8")
    monkeypatch.setenv("GH_TOKEN", "token")

    settings = WorkerSettings(
        workspace_path=str(workspace),
        repo_url=src_repo,
        repo_ref="main",
        repo_dir_name="repo",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex"),
        ready_poll_seconds=0.1,
    )

    with pytest.raises(AgentInitError, match="not a valid git repo"):
        stage_repo_sync(settings)


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


def test_stage_workspace_prepare_leaves_repo_codex_as_project_scope(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    repo_codex = repo_path / ".codex"
    repo_codex.mkdir()
    (repo_codex / "config.toml").write_text("source = 'repo'\n", encoding="utf-8")
    (repo_codex / "repo-only.txt").write_text("repo\n", encoding="utf-8")

    codex_home = tmp_path / "codex-home"
    settings = WorkerSettings(
        workspace_path=str(tmp_path),
        repo_url=str(repo_path),
        repo_ref="main",
        repo_dir_name="src",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(codex_home),
        ready_poll_seconds=0.1,
    )

    stage_workspace_prepare(settings)

    assert not (codex_home / "repo-only.txt").exists()
    assert (repo_path / ".codex" / "config.toml").read_text(encoding="utf-8") == "source = 'repo'\n"


def test_stage_workspace_prepare_leaves_repo_claude_as_project_scope(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    repo_claude = repo_path / ".claude"
    repo_claude.mkdir()
    (repo_claude / "settings.json").write_text('{"source":"repo"}\n', encoding="utf-8")
    (repo_claude / "CLAUDE.md").write_text("# Project instructions\n", encoding="utf-8")

    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))

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

    assert not (home_dir / ".claude" / "settings.json").exists()
    assert not (home_dir / ".claude" / "CLAUDE.md").exists()
    assert (repo_path / ".claude" / "settings.json").read_text(encoding="utf-8") == '{"source":"repo"}\n'


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
