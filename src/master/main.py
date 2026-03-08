from __future__ import annotations

import logging
from threading import Thread

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import load_master_settings
from .discord_app import run_discord_frontend
from .registry import AgentRegistry
from .router import ChannelRouter, MultiAgentDispatcher, PodmanExecDispatcher
from .runtime_adapter import PodmanRuntimeAdapter
from .service import MasterService
from .slack_app import CommandRateLimiter, create_master_app


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.StreamHandler()],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def mask_token(token: str) -> str:
    value = token.strip()
    if not value:
        return "-"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def main() -> None:
    load_dotenv()
    configure_logging()

    settings = load_master_settings()
    logging.getLogger(__name__).info(
        "master.startup frontends=%s registry_path=%s thread_state_path=%s admin_channels=%s discord_admin_channels=%s dry_run=%s base_image=%s auth_json_path=%s ssh_auth_sock_path=%s ssh_known_hosts_path=%s git_user=%s git_email=%s default_adapter=%s bot_token=%s app_token=%s discord_token=%s dispatch_timeout=%s rate_limit=%d/%ds",
        ",".join(sorted(settings.frontends)),
        settings.registry_path,
        settings.thread_state_path,
        ",".join(sorted(settings.admin_channels)),
        ",".join(sorted(settings.discord_admin_channels)),
        settings.dry_run,
        settings.agent_base_image,
        settings.agent_codex_auth_json_path or "-",
        settings.agent_ssh_auth_sock_path or "-",
        settings.agent_ssh_known_hosts_path or "-",
        settings.git_user_name or "-",
        settings.git_user_email or "-",
        settings.default_agent_adapter,
        mask_token(settings.slack_bot_token),
        mask_token(settings.slack_app_token),
        mask_token(settings.discord_bot_token or ""),
        settings.dispatch_timeout_seconds,
        settings.command_rate_limit_count,
        settings.command_rate_limit_window_seconds,
    )
    registry = AgentRegistry(settings.registry_path)
    runtime = PodmanRuntimeAdapter(dry_run=settings.dry_run)
    service = MasterService(
        registry=registry,
        runtime=runtime,
        default_image=settings.agent_base_image,
        agent_codex_auth_json_path=settings.agent_codex_auth_json_path,
        agent_ssh_auth_sock_path=settings.agent_ssh_auth_sock_path,
        agent_ssh_known_hosts_path=settings.agent_ssh_known_hosts_path,
        git_user_name=settings.git_user_name,
        git_user_email=settings.git_user_email,
        default_agent_adapter=settings.default_agent_adapter,
    )
    codex_dispatcher = PodmanExecDispatcher(
        command_template=settings.codex_command_template,
        timeout_seconds=settings.dispatch_timeout_seconds,
        slack_bot_token=settings.slack_bot_token,
    )
    claude_dispatcher = PodmanExecDispatcher(
        command_template=settings.claude_command_template,
        timeout_seconds=settings.dispatch_timeout_seconds,
        slack_bot_token=settings.slack_bot_token,
    )
    dispatcher = MultiAgentDispatcher(
        dispatchers={"codex": codex_dispatcher, "claude-code": claude_dispatcher},
        default_adapter=settings.default_agent_adapter,
    )
    router = ChannelRouter(
        registry=registry,
        dispatcher=dispatcher,
        admin_channels=settings.admin_channels,
        tracked_threads_path=settings.thread_state_path,
    )
    rate_limiter = CommandRateLimiter(
        max_calls=settings.command_rate_limit_count,
        window_seconds=settings.command_rate_limit_window_seconds,
    )

    threads: list[Thread] = []
    if "slack" in settings.frontends:
        app = create_master_app(
            bot_token=settings.slack_bot_token,
            admin_channels=settings.admin_channels,
            service=service,
            router=router,
            rate_limiter=rate_limiter,
        )
        handler = SocketModeHandler(app, settings.slack_app_token)
        slack_thread = Thread(target=handler.start, name="frontend-slack", daemon=True)
        slack_thread.start()
        threads.append(slack_thread)
        logging.getLogger(__name__).info("master.frontend_started name=slack")

    if "discord" in settings.frontends:
        discord_thread = Thread(
            target=run_discord_frontend,
            kwargs={
                "bot_token": settings.discord_bot_token or "",
                "admin_channels": settings.discord_admin_channels,
                "service": service,
                "router": router,
                "rate_limiter": rate_limiter,
            },
            name="frontend-discord",
            daemon=True,
        )
        discord_thread.start()
        threads.append(discord_thread)
        logging.getLogger(__name__).info("master.frontend_started name=discord")

    if not threads:
        raise RuntimeError("No frontends enabled")

    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
