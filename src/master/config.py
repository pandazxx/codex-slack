from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MasterSettings:
    slack_bot_token: str
    slack_app_token: str
    admin_channels: set[str]
    registry_path: str
    dry_run: bool
    agent_base_image: str
    agent_codex_auth_json_path: str | None
    agent_ssh_auth_sock_path: str | None
    agent_ssh_known_hosts_path: str | None
    git_user_name: str | None
    git_user_email: str | None
    dispatch_command_template: str
    command_rate_limit_count: int
    command_rate_limit_window_seconds: int


def _parse_admin_channels(raw_value: str) -> set[str]:
    return {item.strip() for item in raw_value.split(",") if item.strip()}


def _parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def load_master_settings() -> MasterSettings:
    bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    admin_channels = _parse_admin_channels(os.getenv("MASTER_ADMIN_CHANNELS", ""))
    registry_path = os.getenv("MASTER_REGISTRY_PATH", "data/master/agents.json").strip()
    dry_run = _parse_bool(os.getenv("MASTER_DRY_RUN", "false"))
    agent_base_image = os.getenv("MASTER_AGENT_BASE_IMAGE", "codex-slack-bot:latest").strip() or "codex-slack-bot:latest"
    agent_codex_auth_json_path = os.getenv("MASTER_CODEX_AUTH_JSON_PATH", "").strip() or None
    agent_ssh_auth_sock_path = os.getenv("MASTER_SSH_AUTH_SOCK_PATH", "").strip() or None
    agent_ssh_known_hosts_path = os.getenv("MASTER_SSH_KNOWN_HOSTS_PATH", "").strip() or None
    git_user_name = os.getenv("MASTER_GIT_USER_NAME", "").strip() or None
    git_user_email = os.getenv("MASTER_GIT_USER_EMAIL", "").strip() or None
    dispatch_command_template = os.getenv(
        "MASTER_AGENT_COMMAND_TEMPLATE",
        "codex exec --dangerously-bypass-approvals-and-sandbox resume {session_id} -",
    ).strip()
    raw_rate_limit_count = os.getenv("MASTER_COMMAND_RATE_LIMIT_COUNT", "20").strip()
    raw_rate_limit_window = os.getenv("MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS", "60").strip()
    command_rate_limit_count = int(raw_rate_limit_count) if raw_rate_limit_count else 20
    command_rate_limit_window_seconds = int(raw_rate_limit_window) if raw_rate_limit_window else 60
    if command_rate_limit_count < 0:
        command_rate_limit_count = 0
    if command_rate_limit_window_seconds <= 0:
        command_rate_limit_window_seconds = 60

    missing: list[str] = []
    if not bot_token:
        missing.append("SLACK_BOT_TOKEN")
    if not app_token:
        missing.append("SLACK_APP_TOKEN")
    if not admin_channels:
        missing.append("MASTER_ADMIN_CHANNELS")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return MasterSettings(
        slack_bot_token=bot_token,
        slack_app_token=app_token,
        admin_channels=admin_channels,
        registry_path=registry_path,
        dry_run=dry_run,
        agent_base_image=agent_base_image,
        agent_codex_auth_json_path=agent_codex_auth_json_path,
        agent_ssh_auth_sock_path=agent_ssh_auth_sock_path,
        agent_ssh_known_hosts_path=agent_ssh_known_hosts_path,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        dispatch_command_template=dispatch_command_template,
        command_rate_limit_count=command_rate_limit_count,
        command_rate_limit_window_seconds=command_rate_limit_window_seconds,
    )
