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
    )
