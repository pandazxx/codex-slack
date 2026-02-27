from __future__ import annotations

import logging

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import load_master_settings
from .registry import AgentRegistry
from .router import ChannelRouter, PodmanExecDispatcher
from .runtime_adapter import PodmanRuntimeAdapter
from .service import MasterService
from .slack_app import CommandRateLimiter, create_master_app


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.StreamHandler()],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    load_dotenv()
    configure_logging()

    settings = load_master_settings()
    registry = AgentRegistry(settings.registry_path)
    runtime = PodmanRuntimeAdapter(dry_run=settings.dry_run)
    service = MasterService(registry=registry, runtime=runtime)
    dispatcher = PodmanExecDispatcher(
        command_template=settings.dispatch_command_template,
        timeout_seconds=settings.dispatch_timeout_seconds,
    )
    router = ChannelRouter(
        registry=registry,
        dispatcher=dispatcher,
        admin_channels=settings.admin_channels,
    )
    rate_limiter = CommandRateLimiter(
        max_calls=settings.command_rate_limit_count,
        window_seconds=settings.command_rate_limit_window_seconds,
    )

    app = create_master_app(
        bot_token=settings.slack_bot_token,
        admin_channels=settings.admin_channels,
        service=service,
        router=router,
        rate_limiter=rate_limiter,
    )
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    main()
