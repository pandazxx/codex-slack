from __future__ import annotations

import logging
from typing import Protocol

from .command_dispatch import MasterCommandRequest, dispatch_command, parse_optional_name_text
from .command_format import format_command_result, format_status_full_chunks, wants_full_status
from .router import ChannelRouter
from .service import CommandResult, MasterService

LOGGER = logging.getLogger(__name__)


class RateLimiter(Protocol):
    max_calls: int
    window_seconds: int

    def allow(self, key: str) -> bool:
        ...


def is_admin_channel(channel_id: str, admin_channels: set[str]) -> bool:
    return channel_id in admin_channels


def execute_master_command(
    *,
    platform: str,
    command_name: str,
    text: str,
    channel_id: str,
    user_id: str,
    admin_channels: set[str],
    service: MasterService,
    router: ChannelRouter | None = None,
    rate_limiter: RateLimiter | None = None,
) -> list[str]:
    request = MasterCommandRequest(
        command_name=command_name,
        text=text,
        channel_id=channel_id,
        user_id=user_id,
        platform=platform,
    )
    LOGGER.info(
        "master.command_received command=%s channel=%s user=%s",
        command_name,
        channel_id or "-",
        user_id or "-",
    )

    if not is_admin_channel(channel_id, admin_channels):
        LOGGER.warning(
            "master.command_rejected command=%s reason=non_admin_channel channel=%s user=%s admin_channels=%s",
            command_name,
            channel_id or "-",
            user_id or "-",
            ",".join(sorted(admin_channels)),
        )
        result = CommandResult(
            ok=False,
            code="ERR_INVALID_ARGS",
            message="command allowed in admin channel only",
            data={"channel_id": channel_id},
        )
        return [format_command_result(command_name, result)]

    limiter_key = f"{channel_id}:{user_id}"
    if rate_limiter and not rate_limiter.allow(limiter_key):
        LOGGER.warning(
            "master.command_rejected command=%s reason=rate_limited channel=%s user=%s key=%s max_calls=%d window_seconds=%d",
            command_name,
            channel_id or "-",
            user_id or "-",
            limiter_key,
            rate_limiter.max_calls,
            rate_limiter.window_seconds,
        )
        result = CommandResult(
            ok=False,
            code="ERR_RATE_LIMITED",
            message="command rate limited",
            data={"window_seconds": rate_limiter.window_seconds, "max_calls": rate_limiter.max_calls},
        )
        return [format_command_result(command_name, result)]

    try:
        if command_name == "/master-agent-usage":
            if router is None:
                result = CommandResult(
                    ok=False,
                    code="ERR_INTERNAL",
                    message="usage router is not configured",
                    data={},
                )
            else:
                selected_agent = parse_optional_name_text(text)
                result = CommandResult(
                    ok=True,
                    code="OK",
                    message="usage listed",
                    data={"usage": router.usage_summary(selected_agent)},
                )
        else:
            result = dispatch_command(service, request)

        LOGGER.info(
            "master.command_dispatch_done command=%s channel=%s user=%s ok=%s code=%s",
            command_name,
            channel_id or "-",
            user_id or "-",
            result.ok,
            result.code,
        )
        if wants_full_status(command_name, text) and result.ok:
            return format_status_full_chunks(command_name, result)
        return [format_command_result(command_name, result)]
    except ValueError as exc:
        LOGGER.warning(
            "master.command_rejected command=%s reason=invalid_args channel=%s user=%s error=%s text=%r",
            command_name,
            channel_id or "-",
            user_id or "-",
            str(exc),
            text,
        )
        result = CommandResult(ok=False, code="ERR_INVALID_ARGS", message=str(exc), data={})
        return [format_command_result(command_name, result)]
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("master command failed: %s", command_name)
        result = CommandResult(ok=False, code="ERR_INTERNAL", message=str(exc), data={})
        return [format_command_result(command_name, result)]
