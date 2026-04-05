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


def test_stage_workspace_prepare_applies_global_codex_config_as_user_scope(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Global (master) Codex config is installed into CODEX_HOME as user-scope."""
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    global_codex = tmp_path / "global-codex"
    global_codex.mkdir()
    (global_codex / "config.toml").write_text("source = 'global'\n", encoding="utf-8")
    (global_codex / "instructions.md").write_text("global instructions\n", encoding="utf-8")
    (global_codex / "global-only.txt").write_text("global\n", encoding="utf-8")

    monkeypatch.setenv("AGENT_GLOBAL_CODEX_CONFIG_DIR", str(global_codex))

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

    # Global config lands in CODEX_HOME (user scope).
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == "source = 'global'\n"
    assert (codex_home / "instructions.md").read_text(encoding="utf-8") == "global instructions\n"
    assert (codex_home / "global-only.txt").read_text(encoding="utf-8") == "global\n"


def test_stage_workspace_prepare_logs_codex_copy_result(tmp_path, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    global_codex = tmp_path / "global-codex"
    global_codex.mkdir()
    (global_codex / "instructions.md").write_text("global instructions\n", encoding="utf-8")
    (global_codex / "config.toml").write_text("source = 'global'\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_GLOBAL_CODEX_CONFIG_DIR", str(global_codex))

    settings = WorkerSettings(
        workspace_path=str(tmp_path),
        repo_url=str(repo_path),
        repo_ref="main",
        repo_dir_name="src",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex-home"),
        ready_poll_seconds=0.1,
    )

    caplog.set_level("INFO")
    stage_workspace_prepare(settings)

    assert "agent.workspace_prepare_copied" in caplog.text
    assert "target_instructions_md=True" in caplog.text
    assert "target_config_toml=True" in caplog.text


def test_stage_workspace_prepare_logs_codex_copy_skip_state(tmp_path, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    monkeypatch.delenv("AGENT_GLOBAL_CODEX_CONFIG_DIR", raising=False)

    settings = WorkerSettings(
        workspace_path=str(tmp_path),
        repo_url=str(repo_path),
        repo_ref="main",
        repo_dir_name="src",
        status_file=str(tmp_path / "status.json"),
        codex_home=str(tmp_path / "codex-home"),
        ready_poll_seconds=0.1,
    )

    caplog.set_level("INFO")
    stage_workspace_prepare(settings)

    assert "agent.workspace_prepare_copy_skipped" in caplog.text
    assert "reason=missing_global_codex_config" in caplog.text


def test_stage_workspace_prepare_leaves_repo_codex_as_project_scope(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Repo .codex/ is NOT copied into CODEX_HOME; it stays in the repo dir as project-scope
    config, which Codex reads naturally from the working directory and which takes precedence
    over user-scope settings in CODEX_HOME."""
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    global_codex = tmp_path / "global-codex"
    global_codex.mkdir()
    (global_codex / "config.toml").write_text("source = 'global'\n", encoding="utf-8")

    repo_codex = repo_path / ".codex"
    repo_codex.mkdir()
    (repo_codex / "config.toml").write_text("source = 'repo'\n", encoding="utf-8")
    (repo_codex / "repo-only.txt").write_text("repo\n", encoding="utf-8")

    monkeypatch.setenv("AGENT_GLOBAL_CODEX_CONFIG_DIR", str(global_codex))

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

    # Global config stays at user scope; repo config is NOT promoted into CODEX_HOME.
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == "source = 'global'\n"
    assert not (codex_home / "repo-only.txt").exists()
    # Repo's .codex/ is untouched in place (Codex reads it as project scope from CWD).
    assert (repo_path / ".codex" / "config.toml").read_text(encoding="utf-8") == "source = 'repo'\n"


def test_stage_workspace_prepare_leaves_repo_claude_as_project_scope(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Repo .claude/ is NOT copied to ~/.claude/; Claude Code reads it as project-scope config
    from the working directory, which takes precedence over user-scope settings in ~/.claude/."""
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    repo_claude = repo_path / ".claude"
    repo_claude.mkdir()
    (repo_claude / "settings.json").write_text('{"source":"repo"}\n', encoding="utf-8")
    (repo_claude / "CLAUDE.md").write_text("# Project instructions\n", encoding="utf-8")

    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("AGENT_GLOBAL_CLAUDE_CONFIG_DIR", raising=False)

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

    # Repo .claude/ stays in the repo (project scope); nothing is copied into ~/.claude/.
    assert not (home_dir / ".claude" / "settings.json").exists()
    assert not (home_dir / ".claude" / "CLAUDE.md").exists()
    # Repo files are untouched.
    assert (repo_path / ".claude" / "settings.json").read_text(encoding="utf-8") == '{"source":"repo"}\n'


def test_stage_workspace_prepare_applies_global_claude_config_to_home(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo_path = Path(_create_local_repo(tmp_path / "src"))
    global_claude = tmp_path / "global-claude"
    global_claude.mkdir()
    (global_claude / "settings.json").write_text('{"theme":"global"}\n', encoding="utf-8")

    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("AGENT_GLOBAL_CLAUDE_CONFIG_DIR", str(global_claude))

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

    assert (home_dir / ".claude" / "settings.json").read_text(encoding="utf-8") == '{"theme":"global"}\n'


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
