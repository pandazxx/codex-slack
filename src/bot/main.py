from __future__ import annotations

import argparse
import logging
import os
import uuid

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .codex_bridge import LocalCodexBridge
from .config import load_settings
from .runtime import SessionRuntime
from .service import BotService
from .slack_app import create_app

LOGGER = logging.getLogger(__name__)
SECRET_KEY_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASSWD")
STARTUP_ENV_KEYS = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_ALLOWED_CHANNELS",
    "CODEX_WORKSPACE_PATH",
    "BOT_LOG_FILE",
    "CODEX_SESSION_ID",
    "CODEX_TIMEOUT_SECONDS",
    "CODEX_COMMAND_TEMPLATE",
    "CODEX_COMMAND_TEMPLATE_NO_SESSION",
    "OPENAI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Slack to Codex local bridge")
    parser.add_argument("--session-id", help="Local Codex session ID to attach")
    parser.add_argument("--channel", help="Optional single allowed channel override")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args()


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_file = os.getenv("BOT_LOG_FILE", "").strip()
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def resolve_session_context(cli_session_id: str | None) -> tuple[str, bool]:
    configured = cli_session_id or os.getenv("CODEX_SESSION_ID", "").strip()
    if configured:
        return configured, True
    return f"auto-{uuid.uuid4().hex[:12]}", False


def is_secret_env_key(key: str) -> bool:
    upper_key = key.upper()
    return any(marker in upper_key for marker in SECRET_KEY_MARKERS)


def mask_secret_value(value: str) -> str:
    if not value:
        return "(unset)"
    return f"{value[:3]}****{value[-3:]}"


def format_startup_env_value(key: str, value: str) -> str:
    if not value:
        return "(unset)"
    if is_secret_env_key(key):
        return mask_secret_value(value)
    return value


def log_startup_environment() -> None:
    if not LOGGER.isEnabledFor(logging.INFO):
        return
    for key in STARTUP_ENV_KEYS:
        raw_value = os.getenv(key, "").strip()
        LOGGER.info("startup.env %s=%s", key, format_startup_env_value(key, raw_value))


def main() -> None:
    load_dotenv()
    args = parse_args()

    configure_logging(args.log_level)
    log_startup_environment()

    settings = load_settings()
    allowed_channels = {args.channel} if args.channel else settings.allowed_channels
    session_id, explicit_session = resolve_session_context(args.session_id)
    command_template = (
        settings.codex_command_template if explicit_session else settings.codex_command_template_no_session
    )
    LOGGER.info(
        "startup.session explicit=%s session_id=%s template=%r",
        explicit_session,
        session_id,
        command_template,
    )

    if explicit_session:
        LOGGER.info("Using explicit Codex session id=%s", session_id)
    else:
        LOGGER.info(
            "No CODEX_SESSION_ID provided. Starting with generated session id=%s using template=%s",
            session_id,
            command_template,
        )

    runtime = SessionRuntime(initial_session_id=session_id)
    bridge = LocalCodexBridge(
        command_template=command_template,
        timeout_seconds=settings.codex_timeout_seconds,
        workspace_path=settings.codex_workspace_path,
    )
    service = BotService(runtime=runtime, bridge=bridge, allowed_channels=allowed_channels)

    app = create_app(bot_token=settings.slack_bot_token, service=service)
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    main()
