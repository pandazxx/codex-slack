from __future__ import annotations

import logging
import os
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MasterSettings:
    data_dir: str
    dry_run: bool
    agent_base_image: str
    agent_codex_auth_json_path: str | None
    agent_ssh_auth_sock_path: str | None
    agent_ssh_known_hosts_path: str | None
    git_user_name: str | None
    git_user_email: str | None
    mqtt_host: str
    mqtt_port: int
    master_port: int
    container_runtime: str


def _parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _log_env(name: str, value: str | None) -> None:
    if value is None:
        LOGGER.info("master.env %s=<not set> (using default)", name)
    else:
        LOGGER.info("master.env %s=%r", name, value)


def load_master_settings() -> MasterSettings:
    LOGGER.info("master.env_load start")

    data_dir = os.getenv("MASTER_DATA_DIR", "/opt/codex-slack/data/master").strip()
    _log_env("MASTER_DATA_DIR", os.getenv("MASTER_DATA_DIR"))

    dry_run = _parse_bool(os.getenv("MASTER_DRY_RUN", "false"))
    _log_env("MASTER_DRY_RUN", os.getenv("MASTER_DRY_RUN"))

    agent_base_image = os.getenv("MASTER_AGENT_BASE_IMAGE", "codex-slack-master:latest").strip() or "codex-slack-master:latest"
    _log_env("MASTER_AGENT_BASE_IMAGE", os.getenv("MASTER_AGENT_BASE_IMAGE"))

    agent_codex_auth_json_path = os.getenv("MASTER_CODEX_AUTH_JSON_PATH", "").strip() or None
    _log_env("MASTER_CODEX_AUTH_JSON_PATH", os.getenv("MASTER_CODEX_AUTH_JSON_PATH"))

    agent_ssh_auth_sock_path = os.getenv("MASTER_SSH_AUTH_SOCK_PATH", "").strip() or None
    _log_env("MASTER_SSH_AUTH_SOCK_PATH", os.getenv("MASTER_SSH_AUTH_SOCK_PATH"))

    agent_ssh_known_hosts_path = os.getenv("MASTER_SSH_KNOWN_HOSTS_PATH", "").strip() or None
    _log_env("MASTER_SSH_KNOWN_HOSTS_PATH", os.getenv("MASTER_SSH_KNOWN_HOSTS_PATH"))

    git_user_name = os.getenv("MASTER_GIT_USER_NAME", "").strip() or None
    _log_env("MASTER_GIT_USER_NAME", os.getenv("MASTER_GIT_USER_NAME"))

    git_user_email = os.getenv("MASTER_GIT_USER_EMAIL", "").strip() or None
    _log_env("MASTER_GIT_USER_EMAIL", os.getenv("MASTER_GIT_USER_EMAIL"))

    mqtt_host = os.getenv("MQTT_HOST", "mosquitto").strip() or "mosquitto"
    _log_env("MQTT_HOST", os.getenv("MQTT_HOST"))

    raw_mqtt_port = os.getenv("MQTT_PORT", "1883").strip()
    _log_env("MQTT_PORT", os.getenv("MQTT_PORT"))
    mqtt_port = int(raw_mqtt_port) if raw_mqtt_port else 1883

    raw_master_port = os.getenv("MASTER_PORT", "8080").strip()
    _log_env("MASTER_PORT", os.getenv("MASTER_PORT"))
    master_port = int(raw_master_port) if raw_master_port else 8080

    container_runtime = os.getenv("CONTAINER_RUNTIME", "podman").strip().lower() or "podman"
    _log_env("CONTAINER_RUNTIME", os.getenv("CONTAINER_RUNTIME"))
    if container_runtime not in {"podman", "docker"}:
        raise ValueError(f"CONTAINER_RUNTIME must be 'podman' or 'docker', got: {container_runtime!r}")

    LOGGER.info(
        "master.env_load done data_dir=%s dry_run=%s mqtt=%s:%s runtime=%s",
        data_dir, dry_run, mqtt_host, mqtt_port, container_runtime,
    )

    return MasterSettings(
        data_dir=data_dir,
        dry_run=dry_run,
        agent_base_image=agent_base_image,
        agent_codex_auth_json_path=agent_codex_auth_json_path,
        agent_ssh_auth_sock_path=agent_ssh_auth_sock_path,
        agent_ssh_known_hosts_path=agent_ssh_known_hosts_path,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        master_port=master_port,
        container_runtime=container_runtime,
    )
