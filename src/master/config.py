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
    agent_network: str
    agent_codex_auth_json_path: str | None
    agent_ssh_auth_sock_path: str | None
    agent_ssh_known_hosts_path: str | None
    git_user_name: str | None
    git_user_email: str | None
    claude_code_oauth_token: str | None
    anthropic_api_key: str | None
    openai_api_key: str | None
    gh_token: str | None
    mqtt_host: str
    mqtt_port: int
    master_port: int
    container_runtime: str
    attachment_max_size_mb: int = 20
    attachment_data_dir: str = ""
    master_url: str = "http://master:8080"

    @property
    def attachment_max_size_bytes(self) -> int:
        return self.attachment_max_size_mb * 1024 * 1024

    def effective_attachment_data_dir(self) -> str:
        return self.attachment_data_dir or f"{self.data_dir}/attachments"


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

    agent_network = os.getenv("MASTER_AGENT_NETWORK", "codex-slack_internal").strip() or "codex-slack_internal"
    _log_env("MASTER_AGENT_NETWORK", os.getenv("MASTER_AGENT_NETWORK"))

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

    claude_code_oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip() or None
    _log_env("CLAUDE_CODE_OAUTH_TOKEN", "<redacted>" if os.getenv("CLAUDE_CODE_OAUTH_TOKEN") else None)

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "").strip() or None
    _log_env("ANTHROPIC_API_KEY", "<redacted>" if os.getenv("ANTHROPIC_API_KEY") else None)

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip() or None
    _log_env("OPENAI_API_KEY", "<redacted>" if os.getenv("OPENAI_API_KEY") else None)

    gh_token = os.getenv("GH_TOKEN", "").strip() or None
    _log_env("GH_TOKEN", "<redacted>" if os.getenv("GH_TOKEN") else None)

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

    raw_attachment_max_size_mb = os.getenv("ATTACHMENT_MAX_SIZE_MB", "20").strip()
    _log_env("ATTACHMENT_MAX_SIZE_MB", os.getenv("ATTACHMENT_MAX_SIZE_MB"))
    attachment_max_size_mb = int(raw_attachment_max_size_mb) if raw_attachment_max_size_mb else 20

    attachment_data_dir = os.getenv("ATTACHMENT_DATA_DIR", "").strip()
    _log_env("ATTACHMENT_DATA_DIR", os.getenv("ATTACHMENT_DATA_DIR"))

    master_url = os.getenv("MASTER_URL", "http://master:8080").strip() or "http://master:8080"
    _log_env("MASTER_URL", os.getenv("MASTER_URL"))

    LOGGER.info(
        "master.env_load done data_dir=%s dry_run=%s mqtt=%s:%s runtime=%s master_url=%s",
        data_dir, dry_run, mqtt_host, mqtt_port, container_runtime, master_url,
    )

    return MasterSettings(
        data_dir=data_dir,
        dry_run=dry_run,
        agent_base_image=agent_base_image,
        agent_network=agent_network,
        agent_codex_auth_json_path=agent_codex_auth_json_path,
        agent_ssh_auth_sock_path=agent_ssh_auth_sock_path,
        agent_ssh_known_hosts_path=agent_ssh_known_hosts_path,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        claude_code_oauth_token=claude_code_oauth_token,
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        gh_token=gh_token,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        master_port=master_port,
        container_runtime=container_runtime,
        attachment_max_size_mb=attachment_max_size_mb,
        attachment_data_dir=attachment_data_dir,
        master_url=master_url,
    )
