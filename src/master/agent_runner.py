"""Spawn and stop per-workspace agent containers via Docker SDK."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import docker
import docker.errors

LOGGER = logging.getLogger(__name__)

# Placeholder that passes agent preflight for public repos when no real token is set.
_GH_TOKEN_FALLBACK = "public-repo-no-auth-needed"


def container_name(workspace_id: str) -> str:
    return f"codex-agent-{workspace_id}"


def _inject_token(repo_url: str, gh_token: str | None) -> str:
    """Embed a GitHub token into an HTTPS clone URL."""
    if not gh_token:
        return repo_url
    m = re.match(r"(https?://)github\.com/(.+)", repo_url.strip())
    if m:
        return f"{m.group(1)}x-access-token:{gh_token}@github.com/{m.group(2)}"
    return repo_url


def resolve_agent_image(
    *,
    workspace_id: str,
    repo_url: str,
    repo_ref: str = "master",
    default_image: str,
    gh_token: str | None = None,
    dry_run: bool = False,
) -> str:
    """Return the image tag to use for this workspace's agent container.

    Shallow-clones the repo, checks for .prj_assistant/image/Dockerfile, and
    builds a project-specific image when the file is present. Falls back to
    default_image if the clone fails or the Dockerfile is absent.
    """
    if dry_run:
        return default_image

    clone_url = _inject_token(repo_url, gh_token)
    tmp_root = tempfile.mkdtemp(prefix="prj-image-resolve-")
    clone_dest = os.path.join(tmp_root, "repo")
    try:
        refs_to_try = [repo_ref]
        if repo_ref == "master":
            refs_to_try.append("main")
        elif repo_ref == "main":
            refs_to_try.append("master")

        clone_ok = False
        for candidate_ref in refs_to_try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", candidate_ref, clone_url, clone_dest],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                clone_ok = True
                break
            if os.path.exists(clone_dest):
                shutil.rmtree(clone_dest)

        if not clone_ok:
            LOGGER.warning(
                "agent_runner.project_image_clone_failed workspace_id=%s refs=%s error=%s",
                workspace_id, refs_to_try, result.stderr.strip(),
            )
            return default_image

        dockerfile = Path(clone_dest) / ".prj_assistant" / "image" / "Dockerfile"
        if not dockerfile.exists():
            LOGGER.info(
                "agent_runner.project_dockerfile_absent workspace_id=%s using_default=%s",
                workspace_id, default_image,
            )
            return default_image

        image_tag = f"codex-agent-{workspace_id}:latest"
        context_dir = str(Path(clone_dest) / ".prj_assistant" / "image")
        LOGGER.info("agent_runner.building_project_image workspace_id=%s tag=%s", workspace_id, image_tag)
        c = _client()
        c.images.build(path=context_dir, dockerfile="Dockerfile", tag=image_tag, pull=True, rm=True)
        LOGGER.info("agent_runner.built_project_image workspace_id=%s tag=%s", workspace_id, image_tag)
        return image_tag
    except Exception as exc:
        LOGGER.exception("agent_runner.project_image_failed workspace_id=%s error=%s", workspace_id, exc)
        raise
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _client() -> docker.DockerClient:
    return docker.from_env()


def spawn_agent(
    *,
    runtime: str,
    workspace_id: str,
    repo_url: str,
    repo_ref: str = "master",
    image: str,
    mqtt_host: str,
    mqtt_port: int,
    network: str,
    claude_code_oauth_token: str | None = None,
    anthropic_api_key: str | None = None,
    openai_api_key: str | None = None,
    gh_token: str | None = None,
    ssh_auth_sock_path: str | None = None,
    ssh_known_hosts_path: str | None = None,
    dry_run: bool = False,
    master_url: str = "http://master:8080",
    extra_env: dict | None = None,
    mem_limit: str | None = None,
) -> str:
    name = container_name(workspace_id)

    env: dict[str, str] = {
        "WORKSPACE_ID": workspace_id,
        "MQTT_HOST": mqtt_host,
        "MQTT_PORT": str(mqtt_port),
        "AGENT_REPO_URL": repo_url,
        "AGENT_REPO_REF": repo_ref,
        "MASTER_URL": master_url,
    }
    # DB runtime config provides credentials set via the UI (e.g. GH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN).
    # Applied first so explicit MasterSettings values take precedence when both are present.
    if extra_env:
        env.update(extra_env)
    # Explicit params (from MasterSettings / .env) override DB config.
    env["GH_TOKEN"] = gh_token or env.get("GH_TOKEN") or _GH_TOKEN_FALLBACK
    for key, val in [
        ("CLAUDE_CODE_OAUTH_TOKEN", claude_code_oauth_token),
        ("ANTHROPIC_API_KEY", anthropic_api_key),
        ("OPENAI_API_KEY", openai_api_key),
    ]:
        if val:
            env[key] = val

    # Persist claude-code and codex state across container restarts.
    # Named volumes are created automatically by the Docker daemon on first run.
    claude_volume = f"codex-claude-{workspace_id}"
    codex_volume = f"codex-codex-{workspace_id}"
    volumes: dict[str, dict] = {
        claude_volume: {"bind": "/home/appuser/.claude", "mode": "rw"},
        codex_volume: {"bind": "/home/appuser/.codex", "mode": "rw"},
    }
    if ssh_auth_sock_path:
        env["SSH_AUTH_SOCK"] = "/run/ssh-agent.sock"
        # Disable strict host checking so the container doesn't need a pre-seeded known_hosts
        env.setdefault("GIT_SSH_COMMAND", "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null")
        volumes[ssh_auth_sock_path] = {"bind": "/run/ssh-agent.sock", "mode": "ro"}
    if ssh_known_hosts_path:
        # If an explicit known_hosts is provided, prefer it over the no-check fallback
        env["GIT_SSH_COMMAND"] = f"ssh -o UserKnownHostsFile=/run/secrets/known_hosts"
        volumes[ssh_known_hosts_path] = {"bind": "/run/secrets/known_hosts", "mode": "ro"}

    if dry_run:
        LOGGER.info("agent_runner.dry_run_spawn container=%s image=%s", name, image)
        return name

    c = _client()
    try:
        c.containers.get(name).remove(force=True)
    except docker.errors.NotFound:
        pass

    run_kwargs: dict = dict(
        entrypoint=["/usr/bin/tini", "--", "/usr/local/bin/bot-entrypoint"],
        command=["python", "-m", "src.agent.main"],
        name=name,
        network=network,
        environment=env,
        volumes=volumes,
        restart_policy={"Name": "unless-stopped"},
        detach=True,
    )
    if mem_limit:
        run_kwargs["mem_limit"] = mem_limit

    c.containers.run(image, **run_kwargs)
    LOGGER.info(
        "agent_runner.spawned container=%s workspace_id=%s mem_limit=%s",
        name, workspace_id, mem_limit or "(unset)",
    )
    return name


def stop_agent(
    *,
    runtime: str,
    name: str,
    dry_run: bool = False,
) -> None:
    if dry_run:
        LOGGER.info("agent_runner.dry_run_stop container=%s", name)
        return

    c = _client()
    try:
        c.containers.get(name).remove(force=True)
        LOGGER.info("agent_runner.stopped container=%s", name)
    except docker.errors.NotFound:
        pass


def pause_agent(*, name: str, dry_run: bool = False) -> None:
    """Stop (but do not remove) a container so it can be restarted later."""
    if dry_run:
        LOGGER.info("agent_runner.dry_run_pause container=%s", name)
        return
    c = _client()
    try:
        container = c.containers.get(name)
        if container.status != "running":
            return
        container.stop()
        LOGGER.info("agent_runner.paused container=%s", name)
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        LOGGER.warning("agent_runner.pause_failed name=%s error=%s", name, exc)


def start_agent_if_stopped(*, name: str, dry_run: bool = False) -> bool:
    """Start a stopped/exited container without recreating it. Returns True if started."""
    if dry_run:
        LOGGER.info("agent_runner.dry_run_start container=%s", name)
        return False
    c = _client()
    try:
        container = c.containers.get(name)
        if container.status == "running":
            return False
        container.start()
        LOGGER.info("agent_runner.restarted container=%s", name)
        return True
    except docker.errors.NotFound:
        return False
    except Exception as exc:
        LOGGER.warning("agent_runner.start_failed name=%s error=%s", name, exc)
        return False


def refresh_auth(*, name: str, gh_token: str | None, dry_run: bool = False) -> None:
    """Re-run auth setup inside a running agent container."""
    if dry_run:
        LOGGER.info("agent_runner.dry_run_refresh_auth container=%s", name)
        return
    c = _client()
    try:
        container = c.containers.get(name)
    except docker.errors.NotFound:
        LOGGER.warning("agent_runner.refresh_auth_skip container=%s reason=not_found", name)
        return
    if container.status != "running":
        LOGGER.warning("agent_runner.refresh_auth_skip container=%s reason=status=%s", name, container.status)
        return
    if gh_token:
        try:
            exit_code, _ = container.exec_run(
                ["gh", "auth", "setup-git"],
                environment={"GH_TOKEN": gh_token},
            )
            LOGGER.info("agent_runner.refresh_auth container=%s gh_exit=%d", name, exit_code)
        except Exception:
            LOGGER.exception("agent_runner.refresh_auth_failed container=%s", name)


def _parse_app_version(env_list: list[str]) -> str | None:
    for item in env_list:
        if item.startswith("APP_VERSION="):
            val = item[len("APP_VERSION="):].strip()
            return val or None
    return None


def get_container_status(*, name: str, dry_run: bool = False) -> dict:  # type: ignore[type-arg]
    if dry_run:
        return {"status": "dry_run", "exit_code": None, "restart_count": None, "error": None, "version": None}
    try:
        c = _client()
        container = c.containers.get(name)
        state = container.attrs.get("State", {})
        env_list = container.attrs.get("Config", {}).get("Env") or []
        return {
            "status": container.status,
            "exit_code": state.get("ExitCode"),
            "restart_count": container.attrs.get("RestartCount", 0),
            "error": state.get("Error") or None,
            "version": _parse_app_version(env_list),
        }
    except docker.errors.NotFound:
        return {"status": "not_found", "exit_code": None, "restart_count": None, "error": None, "version": None}
    except Exception as exc:
        LOGGER.warning("agent_runner.inspect_failed name=%s error=%s", name, exc)
        return {"status": "unknown", "exit_code": None, "restart_count": None, "error": str(exc), "version": None}
