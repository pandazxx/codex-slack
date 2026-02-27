from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Callable

from slack_bolt import App

from .service import CommandResult, MasterService

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlackCommandRequest:
    command_name: str
    text: str
    channel_id: str
    user_id: str


def is_admin_channel(channel_id: str, admin_channels: set[str]) -> bool:
    return channel_id in admin_channels


def format_command_result(command_name: str, result: CommandResult) -> str:
    payload = {
        "ok": result.ok,
        "command": command_name,
        "code": result.code,
        "message": result.message,
        "data": result.data,
    }
    return json.dumps(payload, sort_keys=True)


def parse_load_text(text: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in text.split() if part.strip()]
    if len(parts) != 3:
        raise ValueError("usage: /master-agent-load <name> <repo_path> <channel_id>")
    return parts[0], parts[1], parts[2]


def parse_single_name_text(text: str, command_name: str) -> str:
    name = text.strip()
    if not name:
        raise ValueError(f"usage: {command_name} <name>")
    return name


def dispatch_slash_command(service: MasterService, request: SlackCommandRequest) -> CommandResult:
    if request.command_name == "/master-agent-list":
        return service.list_agents()

    if request.command_name == "/master-agent-load":
        name, repo_path, channel_id = parse_load_text(request.text)
        return service.load_agent(name=name, repo_path=repo_path, channel_id=channel_id)

    if request.command_name == "/master-agent-start":
        name = parse_single_name_text(request.text, request.command_name)
        return service.start_agent(name=name)

    if request.command_name == "/master-agent-stop":
        name = parse_single_name_text(request.text, request.command_name)
        return service.stop_agent(name=name)

    if request.command_name == "/master-agent-status":
        name = parse_single_name_text(request.text, request.command_name)
        return service.status(name=name)

    if request.command_name == "/master-agent-remove":
        name = parse_single_name_text(request.text, request.command_name)
        return service.remove_agent(name=name)

    return CommandResult(ok=False, code="ERR_INVALID_ARGS", message=f"unsupported command: {request.command_name}", data={})


def _register_command(
    app: App,
    *,
    command_name: str,
    admin_channels: set[str],
    service: MasterService,
) -> None:
    @app.command(command_name)
    def on_command(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()

        request = SlackCommandRequest(
            command_name=command_name,
            text=command.get("text", ""),
            channel_id=command.get("channel_id", ""),
            user_id=command.get("user_id", ""),
        )

        if not is_admin_channel(request.channel_id, admin_channels):
            respond(
                format_command_result(
                    command_name,
                    CommandResult(
                        ok=False,
                        code="ERR_INVALID_ARGS",
                        message="command allowed in admin channel only",
                        data={"channel_id": request.channel_id},
                    ),
                )
            )
            return

        try:
            result = dispatch_slash_command(service, request)
            respond(format_command_result(command_name, result))
        except ValueError as exc:
            respond(
                format_command_result(
                    command_name,
                    CommandResult(ok=False, code="ERR_INVALID_ARGS", message=str(exc), data={}),
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("master slash command failed: %s", command_name)
            respond(
                format_command_result(
                    command_name,
                    CommandResult(ok=False, code="ERR_INTERNAL", message=str(exc), data={}),
                )
            )


def create_master_app(*, bot_token: str, admin_channels: set[str], service: MasterService) -> App:
    app = App(token=bot_token)

    for command_name in (
        "/master-agent-list",
        "/master-agent-load",
        "/master-agent-start",
        "/master-agent-stop",
        "/master-agent-status",
        "/master-agent-remove",
    ):
        _register_command(app, command_name=command_name, admin_channels=admin_channels, service=service)

    return app
