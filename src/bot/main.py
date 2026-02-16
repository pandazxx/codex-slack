from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .codex_bridge import LocalCodexBridge
from .config import load_settings
from .runtime import SessionRuntime
from .service import BotService
from .slack_app import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Slack to Codex local bridge")
    parser.add_argument("--session-id", required=True, help="Local Codex session ID to attach")
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


def main() -> None:
    load_dotenv()
    args = parse_args()

    configure_logging(args.log_level)

    settings = load_settings()
    allowed_channels = {args.channel} if args.channel else settings.allowed_channels

    runtime = SessionRuntime(initial_session_id=args.session_id)
    bridge = LocalCodexBridge(
        command_template=settings.codex_command_template,
        timeout_seconds=settings.codex_timeout_seconds,
        workspace_path=settings.codex_workspace_path,
    )
    service = BotService(runtime=runtime, bridge=bridge, allowed_channels=allowed_channels)

    app = create_app(bot_token=settings.slack_bot_token, service=service)
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    main()
