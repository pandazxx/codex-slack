from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    slack_bot_token: str
    slack_app_token: str
    allowed_channels: set[str]
    codex_workspace_path: str
    codex_command_template: str
    codex_command_template_no_session: str
    codex_timeout_seconds: int | None = None


def _parse_allowed_channels(raw_value: str) -> set[str]:
    return {item.strip() for item in raw_value.split(",") if item.strip()}


def load_settings() -> Settings:
    bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    workspace_path = os.getenv("CODEX_WORKSPACE_PATH", "").strip()
    allowed_channels = _parse_allowed_channels(os.getenv("SLACK_ALLOWED_CHANNELS", ""))
    command_template = os.getenv(
        "CODEX_COMMAND_TEMPLATE",
        "codex exec resume {session_id} -",
    ).strip()
    command_template_no_session = os.getenv(
        "CODEX_COMMAND_TEMPLATE_NO_SESSION",
        "codex exec -",
    ).strip()
    raw_timeout = os.getenv("CODEX_TIMEOUT_SECONDS", "").strip()
    timeout_seconds = int(raw_timeout) if raw_timeout else None
    if timeout_seconds is not None and timeout_seconds <= 0:
        timeout_seconds = None

    missing = []
    if not bot_token:
        missing.append("SLACK_BOT_TOKEN")
    if not app_token:
        missing.append("SLACK_APP_TOKEN")
    if not workspace_path:
        missing.append("CODEX_WORKSPACE_PATH")
    if not allowed_channels:
        missing.append("SLACK_ALLOWED_CHANNELS")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return Settings(
        slack_bot_token=bot_token,
        slack_app_token=app_token,
        allowed_channels=allowed_channels,
        codex_workspace_path=workspace_path,
        codex_command_template=command_template,
        codex_command_template_no_session=command_template_no_session,
        codex_timeout_seconds=timeout_seconds,
    )
