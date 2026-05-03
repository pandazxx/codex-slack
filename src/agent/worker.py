from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import time

from .mqtt_loop import run_mqtt_loop

LOGGER = logging.getLogger(__name__)


class AgentInitError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class WorkerSettings:
    workspace_path: str
    repo_url: str
    repo_ref: str
    repo_dir_name: str
    status_file: str
    codex_home: str
    ready_poll_seconds: float
    workspace_id: str = ""
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    master_url: str = "http://master:8080"


def load_worker_settings() -> WorkerSettings:
    return WorkerSettings(
        workspace_path=os.getenv("CODEX_WORKSPACE_PATH", "/workspace").strip(),
        repo_url=os.getenv("AGENT_REPO_URL", "").strip(),
        repo_ref=os.getenv("AGENT_REPO_REF", "main").strip(),
        repo_dir_name=os.getenv("AGENT_REPO_DIR", "repo").strip(),
        status_file=os.getenv("AGENT_STATUS_FILE", "/tmp/master-agent/status.json").strip(),
        codex_home=os.getenv("CODEX_HOME", "/home/appuser/.codex").strip(),
        ready_poll_seconds=float(os.getenv("AGENT_READY_POLL_SECONDS", "5")),
        workspace_id=os.getenv("WORKSPACE_ID", "").strip(),
        mqtt_host=os.getenv("MQTT_HOST", "localhost").strip(),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        master_url=os.getenv("MASTER_URL", "http://master:8080").strip() or "http://master:8080",
    )


def _stage_event(stage: str, status: str, message: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "component": "agent-worker",
        "stage": stage,
        "status": status,
        "message": message,
    }
    payload.update(extra)
    return payload


def emit_stage_event(stage: str, status: str, message: str, **extra: object) -> None:
    payload = _stage_event(stage=stage, status=status, message=message, **extra)
    LOGGER.info("agent.stage %s", json.dumps(payload, sort_keys=True))


def write_status(path: str, payload: dict[str, object]) -> None:
    status_path = Path(path)
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("agent.status_write_failed path=%s error=%s", path, exc)


def _run_git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git command failed: {' '.join(args)}")
    return completed


def stage_preflight(settings: WorkerSettings) -> None:
    workspace = Path(settings.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    auth_ok = False
    ssh_sock = os.getenv("SSH_AUTH_SOCK", "").strip()
    if ssh_sock and Path(ssh_sock).exists():
        auth_ok = True

    gh_token = os.getenv("GH_TOKEN", "").strip() or os.getenv("GITHUB_TOKEN", "").strip()
    gh_token_file = os.getenv("GH_TOKEN_FILE", "").strip()
    if gh_token:
        auth_ok = True
    if gh_token_file:
        token_path = Path(gh_token_file)
        if not token_path.is_absolute():
            raise AgentInitError("preflight", "GH_TOKEN_FILE must be an absolute path")
        if not token_path.exists():
            raise AgentInitError("preflight", "GH_TOKEN_FILE path does not exist")
        if not token_path.is_file():
            raise AgentInitError("preflight", "GH_TOKEN_FILE path is not a file")
        auth_ok = True

    if not auth_ok:
        raise AgentInitError("preflight", "missing auth source: SSH_AUTH_SOCK or GH token")



def stage_repo_sync(settings: WorkerSettings) -> Path:
    if not settings.repo_url:
        raise AgentInitError("repo_sync", "AGENT_REPO_URL is required")

    repo_dir = Path(settings.workspace_path) / settings.repo_dir_name
    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["git", "clone", "--branch", settings.repo_ref, settings.repo_url, str(repo_dir)])
        return repo_dir

    try:
        completed = _run_git(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(repo_dir))
    except RuntimeError as exc:
        raise AgentInitError("repo_sync", f"existing repo path is not a valid git repo: {repo_dir} ({exc})") from exc

    if completed.stdout.strip() != "true":
        raise AgentInitError("repo_sync", f"existing repo path is not a valid git repo: {repo_dir}")

    LOGGER.info("agent.repo_sync_existing_repo repo_dir=%s action=noop", repo_dir)
    return repo_dir


def stage_workspace_prepare(settings: WorkerSettings) -> None:
    home_dir = Path(os.getenv("HOME", "/home/appuser")).expanduser()
    home_dir.mkdir(parents=True, exist_ok=True)
    xdg_config_home = Path(os.getenv("XDG_CONFIG_HOME", str(home_dir / ".config"))).expanduser()
    xdg_config_home.mkdir(parents=True, exist_ok=True)
    codex_home = Path(settings.codex_home)
    codex_home.mkdir(parents=True, exist_ok=True)
    repo_dir = Path(settings.workspace_path) / settings.repo_dir_name
    repo_codex_dir = repo_dir / ".codex"
    home_claude_dir = home_dir / ".claude"

    LOGGER.info(
        "agent.workspace_prepare_paths codex_home=%s repo_codex_dir=%s home_claude_dir=%s",
        codex_home,
        repo_codex_dir,
        home_claude_dir,
    )
    LOGGER.info(
        "agent.workspace_prepare_state codex_home_exists=%s codex_home_entries=%d codex_home_agents_md=%s codex_home_config_toml=%s home_claude_dir=%s home_claude_exists=%s home_claude_entries=%d home_claude_md=%s home_claude_settings_json=%s",
        codex_home.exists(),
        sum(1 for _ in codex_home.rglob("*")) if codex_home.exists() else 0,
        (codex_home / "AGENTS.md").exists(),
        (codex_home / "config.toml").exists(),
        home_claude_dir,
        home_claude_dir.exists(),
        sum(1 for _ in home_claude_dir.rglob("*")) if home_claude_dir.exists() else 0,
        (home_claude_dir / "CLAUDE.md").exists(),
        (home_claude_dir / "settings.json").exists(),
    )

    # repo_codex_dir (.codex/ in the cloned repo) is intentionally left in place.
    # Codex reads it as project-scope config from the working directory, which takes
    # precedence over user-scope settings in CODEX_HOME per the Codex scope hierarchy.
    LOGGER.info(
        "agent.workspace_prepare_repo_scope repo_codex_dir=%s exists=%s entries=%d repo_agents_md=%s repo_config_toml=%s",
        repo_codex_dir,
        repo_codex_dir.exists(),
        sum(1 for _ in repo_codex_dir.rglob("*")) if repo_codex_dir.exists() else 0,
        (repo_codex_dir / "AGENTS.md").exists(),
        (repo_codex_dir / "config.toml").exists(),
    )
    git_user_name = os.getenv("AGENT_GIT_USER_NAME", "").strip()
    git_user_email = os.getenv("AGENT_GIT_USER_EMAIL", "").strip()

    if repo_dir.joinpath(".git").exists():
        if git_user_name:
            _run_git(["git", "config", "user.name", git_user_name], cwd=str(repo_dir))
        if git_user_email:
            _run_git(["git", "config", "user.email", git_user_email], cwd=str(repo_dir))



def stage_mqtt_loop(settings: WorkerSettings, repo_dir: str) -> None:
    if not settings.workspace_id:
        LOGGER.warning("agent.no_workspace_id — falling back to idle poll loop")
        while True:
            time.sleep(settings.ready_poll_seconds)
        return
    run_mqtt_loop(
        workspace_id=settings.workspace_id,
        mqtt_host=settings.mqtt_host,
        mqtt_port=settings.mqtt_port,
        repo_dir=repo_dir,
        master_url=settings.master_url,
    )



def run_worker(settings: WorkerSettings) -> int:
    try:
        LOGGER.info(
            "agent.worker_settings workspace_path=%s repo_url=%s repo_ref=%s repo_dir_name=%s status_file=%s codex_home=%s ready_poll_seconds=%s",
            settings.workspace_path,
            settings.repo_url or "-",
            settings.repo_ref,
            settings.repo_dir_name,
            settings.status_file,
            settings.codex_home,
            settings.ready_poll_seconds,
        )
        emit_stage_event("preflight", "start", "starting preflight checks")
        stage_preflight(settings)
        emit_stage_event("preflight", "ok", "preflight checks passed")

        emit_stage_event("repo_sync", "start", "starting repo sync")
        repo_dir = stage_repo_sync(settings)
        emit_stage_event("repo_sync", "ok", "repo sync complete", repo_dir=str(repo_dir))

        emit_stage_event("workspace_prepare", "start", "preparing workspace")
        stage_workspace_prepare(settings)
        emit_stage_event("workspace_prepare", "ok", "workspace prepared")

        write_status(
            settings.status_file,
            {
                "stage": "ready",
                "status": "ok",
                "message": "agent worker initialized",
                "repo_ref": settings.repo_ref,
            },
        )
        emit_stage_event("ready", "ok", "agent worker ready")
        stage_mqtt_loop(settings, repo_dir=str(repo_dir))
        return 0
    except AgentInitError as exc:
        write_status(
            settings.status_file,
            {
                "stage": exc.stage,
                "status": "error",
                "message": str(exc),
            },
        )
        emit_stage_event(exc.stage, "error", str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        write_status(
            settings.status_file,
            {
                "stage": "internal",
                "status": "error",
                "message": str(exc),
            },
        )
        emit_stage_event("internal", "error", str(exc))
        return 1
