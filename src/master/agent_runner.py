"""Spawn and stop per-workspace agent containers via Docker SDK."""
from __future__ import annotations

import logging

import docker
import docker.errors

LOGGER = logging.getLogger(__name__)

# Placeholder that passes agent preflight for public repos when no real token is set.
_GH_TOKEN_FALLBACK = "public-repo-no-auth-needed"


def container_name(workspace_id: str) -> str:
    return f"codex-agent-{workspace_id}"


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

    # Persist claude-code session state across container restarts.
    # Named volume is created automatically by the Docker daemon on first run.
    claude_volume = f"codex-claude-{workspace_id}"
    volumes: dict[str, dict] = {
        claude_volume: {"bind": "/home/appuser/.claude", "mode": "rw"},
    }
    if ssh_auth_sock_path:
        env["SSH_AUTH_SOCK"] = "/run/secrets/ssh-auth.sock"
        # Disable strict host checking so the container doesn't need a pre-seeded known_hosts
        env.setdefault("GIT_SSH_COMMAND", "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null")
        volumes[ssh_auth_sock_path] = {"bind": "/run/secrets/ssh-auth.sock", "mode": "ro"}
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

    c.containers.run(
        image,
        command=["python", "-m", "src.agent.main"],
        name=name,
        network=network,
        environment=env,
        volumes=volumes,
        restart_policy={"Name": "unless-stopped"},
        detach=True,
    )
    LOGGER.info("agent_runner.spawned container=%s workspace_id=%s", name, workspace_id)
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


def get_container_status(*, name: str, dry_run: bool = False) -> dict:  # type: ignore[type-arg]
    if dry_run:
        return {"status": "dry_run", "exit_code": None, "restart_count": None, "error": None}
    try:
        c = _client()
        container = c.containers.get(name)
        state = container.attrs.get("State", {})
        return {
            "status": container.status,
            "exit_code": state.get("ExitCode"),
            "restart_count": container.attrs.get("RestartCount", 0),
            "error": state.get("Error") or None,
        }
    except docker.errors.NotFound:
        return {"status": "not_found", "exit_code": None, "restart_count": None, "error": None}
    except Exception as exc:
        LOGGER.warning("agent_runner.inspect_failed name=%s error=%s", name, exc)
        return {"status": "unknown", "exit_code": None, "restart_count": None, "error": str(exc)}
