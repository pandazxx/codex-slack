from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CdSettings:
    # Which image to track (e.g. "ghcr.io/org/codex-slack-master").
    image: str
    # Tag to watch — "latest" for staging, a semver like "v1.2.3" for prod.
    image_tag: str
    # Name of the running master container managed by this daemon.
    container_name: str
    # Path to the docker-compose / podman-compose file that defines the master service.
    compose_file: str
    # Service name inside that compose file.
    compose_service: str
    # Compose binary to invoke, e.g. "docker compose" or "podman-compose".
    compose_binary: str
    # Optional .env file passed to compose via --env-file.
    env_file: str | None
    # JSON file where the daemon persists its deploy state across restarts.
    state_file: str
    # How often (seconds) to poll the registry for a new image digest.
    poll_interval_seconds: int
    # How many seconds to wait after (re)starting the container before checking health.
    health_check_delay_seconds: int
    # Automatically roll back to the previous image on health-check failure.
    rollback_on_failure: bool
    # Skip all side-effecting commands (pull / deploy / rollback).
    dry_run: bool
    # Optional Slack incoming-webhook URL for deploy/rollback notifications.
    notify_slack_webhook_url: str | None
    # Optional Discord incoming-webhook URL for deploy/rollback notifications.
    notify_discord_webhook_url: str | None


def _bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_cd_settings() -> CdSettings:
    image = os.getenv("CD_IMAGE", "").strip()
    if not image:
        raise ValueError("CD_IMAGE is required")

    image_tag = os.getenv("CD_IMAGE_TAG", "latest").strip() or "latest"
    container_name = os.getenv("CD_CONTAINER_NAME", "codex-slack-master").strip()
    compose_file = os.getenv("CD_COMPOSE_FILE", "docker-compose.master-agent.example.yml").strip()
    compose_service = os.getenv("CD_COMPOSE_SERVICE", "codex-slack-master").strip()
    compose_binary = os.getenv("CD_COMPOSE_BINARY", "docker compose").strip()
    env_file = os.getenv("CD_ENV_FILE", "").strip() or None
    state_file = os.getenv("CD_STATE_FILE", "data/cd/state.json").strip()
    poll_interval_seconds = int(os.getenv("CD_POLL_INTERVAL_SECONDS", "300"))
    health_check_delay_seconds = int(os.getenv("CD_HEALTH_CHECK_DELAY_SECONDS", "30"))
    rollback_on_failure = _bool(os.getenv("CD_ROLLBACK_ON_FAILURE", "true"))
    dry_run = _bool(os.getenv("CD_DRY_RUN", "false"))
    notify_slack_webhook_url = os.getenv("CD_NOTIFY_SLACK_WEBHOOK_URL", "").strip() or None
    notify_discord_webhook_url = os.getenv("CD_NOTIFY_DISCORD_WEBHOOK_URL", "").strip() or None

    return CdSettings(
        image=image,
        image_tag=image_tag,
        container_name=container_name,
        compose_file=compose_file,
        compose_service=compose_service,
        compose_binary=compose_binary,
        env_file=env_file,
        state_file=state_file,
        poll_interval_seconds=poll_interval_seconds,
        health_check_delay_seconds=health_check_delay_seconds,
        rollback_on_failure=rollback_on_failure,
        dry_run=dry_run,
        notify_slack_webhook_url=notify_slack_webhook_url,
        notify_discord_webhook_url=notify_discord_webhook_url,
    )
