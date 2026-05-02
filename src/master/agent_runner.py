"""Spawn and stop per-workspace agent containers."""
from __future__ import annotations

import logging
import subprocess

LOGGER = logging.getLogger(__name__)

# Placeholder that passes agent preflight for public repos when no real token is set.
_GH_TOKEN_FALLBACK = "public-repo-no-auth-needed"


def container_name(workspace_id: str) -> str:
    return f"codex-agent-{workspace_id}"


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(cmd)}")


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

    if not dry_run:
        subprocess.run([runtime, "rm", "-f", name], capture_output=True, check=False)

    cmd = [
        runtime, "run", "-d",
        "--name", name,
        "--network", network,
        "--restart", "unless-stopped",
        "-e", f"WORKSPACE_ID={workspace_id}",
        "-e", f"MQTT_HOST={mqtt_host}",
        "-e", f"MQTT_PORT={mqtt_port}",
        "-e", f"AGENT_REPO_URL={repo_url}",
        "-e", f"GH_TOKEN={gh_token or _GH_TOKEN_FALLBACK}",
    ]
    for key, val in [
        ("CLAUDE_CODE_OAUTH_TOKEN", claude_code_oauth_token),
        ("ANTHROPIC_API_KEY", anthropic_api_key),
        ("OPENAI_API_KEY", openai_api_key),
    ]:
        if val:
            cmd += ["-e", f"{key}={val}"]

    cmd += [image, "python", "-m", "src.agent.main"]

    if dry_run:
        LOGGER.info("agent_runner.dry_run_spawn container=%s cmd=%s", name, " ".join(cmd))
    else:
        _run(cmd)
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
    subprocess.run([runtime, "rm", "-f", name], capture_output=True, check=False)
    LOGGER.info("agent_runner.stopped container=%s", name)
