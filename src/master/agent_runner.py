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
    image: str,
    mqtt_host: str,
    mqtt_port: int,
    network: str,
    claude_code_oauth_token: str | None = None,
    anthropic_api_key: str | None = None,
    openai_api_key: str | None = None,
    gh_token: str | None = None,
    dry_run: bool = False,
) -> str:
    name = container_name(workspace_id)

    env = {
        "WORKSPACE_ID": workspace_id,
        "MQTT_HOST": mqtt_host,
        "MQTT_PORT": str(mqtt_port),
        "AGENT_REPO_URL": repo_url,
        "GH_TOKEN": gh_token or _GH_TOKEN_FALLBACK,
    }
    for key, val in [
        ("CLAUDE_CODE_OAUTH_TOKEN", claude_code_oauth_token),
        ("ANTHROPIC_API_KEY", anthropic_api_key),
        ("OPENAI_API_KEY", openai_api_key),
    ]:
        if val:
            env[key] = val

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
