from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
import time

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


def load_worker_settings() -> WorkerSettings:
    return WorkerSettings(
        workspace_path=os.getenv("CODEX_WORKSPACE_PATH", "/workspace").strip(),
        repo_url=os.getenv("AGENT_REPO_URL", "").strip(),
        repo_ref=os.getenv("AGENT_REPO_REF", "main").strip(),
        repo_dir_name=os.getenv("AGENT_REPO_DIR", "repo").strip(),
        status_file=os.getenv("AGENT_STATUS_FILE", "/run/master-agent/status.json").strip(),
        codex_home=os.getenv("CODEX_HOME", "/home/appuser/.codex").strip(),
        ready_poll_seconds=float(os.getenv("AGENT_READY_POLL_SECONDS", "5")),
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
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    if gh_token_file and Path(gh_token_file).exists():
        auth_ok = True

    if not auth_ok:
        raise AgentInitError("preflight", "missing auth source: SSH_AUTH_SOCK or GH token")



def stage_repo_sync(settings: WorkerSettings) -> Path:
    if not settings.repo_url:
        raise AgentInitError("repo_sync", "AGENT_REPO_URL is required")

    repo_dir = Path(settings.workspace_path) / settings.repo_dir_name
    git_dir = repo_dir / ".git"

    if not git_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["git", "clone", "--branch", settings.repo_ref, settings.repo_url, str(repo_dir)])
        return repo_dir

    _run_git(["git", "fetch", "origin", settings.repo_ref], cwd=str(repo_dir))
    _run_git(["git", "checkout", settings.repo_ref], cwd=str(repo_dir))
    _run_git(["git", "reset", "--hard", f"origin/{settings.repo_ref}"], cwd=str(repo_dir))
    return repo_dir


def stage_workspace_prepare(settings: WorkerSettings) -> None:
    codex_home = Path(settings.codex_home)
    codex_home.mkdir(parents=True, exist_ok=True)



def stage_ready(settings: WorkerSettings) -> None:
    while True:
        time.sleep(settings.ready_poll_seconds)



def run_worker(settings: WorkerSettings) -> int:
    try:
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
        stage_ready(settings)
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
