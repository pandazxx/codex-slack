from __future__ import annotations

import logging

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import load_master_settings
from .registry import AgentRegistry
from .runtime_adapter import PodmanRuntimeAdapter
from .service import MasterService
from .slack_app import create_master_app


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

    app = create_master_app(
        bot_token=settings.slack_bot_token,
        admin_channels=settings.admin_channels,
        service=service,
    )
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    main()
