from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from threading import Lock
import time

from slack_bolt import App

from .router import ChannelRouter, RouteError, RouteSkip
from .service import CommandResult, MasterService

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlackCommandRequest:
    command_name: str
    text: str
    channel_id: str
    user_id: str


@dataclass
class CommandRateLimiter:
    max_calls: int
    window_seconds: int
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _events: dict[str, list[float]] = field(default_factory=dict, init=False, repr=False)

    def allow(self, key: str) -> bool:
        if self.max_calls <= 0:
            return True
        now = time.time()
        lower_bound = now - self.window_seconds
        with self._lock:
            history = [ts for ts in self._events.get(key, []) if ts >= lower_bound]
            if len(history) >= self.max_calls:
                self._events[key] = history
                return False
            history.append(now)
            self._events[key] = history
            return True


def is_admin_channel(channel_id: str, admin_channels: set[str]) -> bool:
    return channel_id in admin_channels


def format_command_result(command_name: str, result: CommandResult) -> str:
    status_icon = ":white_check_mark:" if result.ok else ":x:"
    lines = [
        f"{status_icon} *{command_name}*",
        f"*Code:* `{result.code}`",
        f"*Message:* {result.message}",
    ]
    if result.data:
        data_json = json.dumps(result.data, indent=2, sort_keys=True)
        if len(data_json) > 1200:
            data_json = data_json[:1197] + "..."
        lines.append("*Data:*")
        lines.append(f"```json\n{data_json}\n```")
    return "\n".join(lines)


def parse_load_text(text: str) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in text.split() if part.strip()]
    if len(parts) not in {3, 4}:
        raise ValueError("usage: /master-agent-load <name> <repo_path> <channel_id> [branch]")
    repo_ref = parts[3] if len(parts) == 4 else "main"
    return parts[0], parts[1], parts[2], repo_ref


def parse_single_name_text(text: str, command_name: str) -> str:
    name = text.strip()
    if not name:
        raise ValueError(f"usage: {command_name} <name>")
    return name


def dispatch_slash_command(service: MasterService, request: SlackCommandRequest) -> CommandResult:
    if request.command_name == "/master-agent-list":
        return service.list_agents()

    if request.command_name == "/master-agent-load":
        name, repo_path, channel_id, repo_ref = parse_load_text(request.text)
        return service.load_agent(name=name, repo_path=repo_path, channel_id=channel_id, repo_ref=repo_ref)

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

    if request.command_name == "/master-agent-refresh-auth":
        name = parse_single_name_text(request.text, request.command_name)
        return service.refresh_agent_auth(name=name)

    return CommandResult(ok=False, code="ERR_INVALID_ARGS", message=f"unsupported command: {request.command_name}", data={})


def _register_command(
    app: App,
    *,
    command_name: str,
    admin_channels: set[str],
    service: MasterService,
    rate_limiter: CommandRateLimiter | None = None,
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
        LOGGER.info(
            "master.command_received command=%s channel=%s user=%s",
            command_name,
            request.channel_id,
            request.user_id or "-",
        )

        if not is_admin_channel(request.channel_id, admin_channels):
            LOGGER.warning(
                "master.command_rejected command=%s reason=non_admin_channel channel=%s user=%s admin_channels=%s",
                command_name,
                request.channel_id or "-",
                request.user_id or "-",
                ",".join(sorted(admin_channels)),
            )
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

        limiter_key = f"{request.channel_id}:{request.user_id}"
        if rate_limiter and not rate_limiter.allow(limiter_key):
            LOGGER.warning(
                "master.command_rejected command=%s reason=rate_limited channel=%s user=%s key=%s max_calls=%d window_seconds=%d",
                command_name,
                request.channel_id or "-",
                request.user_id or "-",
                limiter_key,
                rate_limiter.max_calls,
                rate_limiter.window_seconds,
            )
            respond(
                format_command_result(
                    command_name,
                    CommandResult(
                        ok=False,
                        code="ERR_RATE_LIMITED",
                        message="command rate limited",
                        data={"window_seconds": rate_limiter.window_seconds, "max_calls": rate_limiter.max_calls},
                    ),
                )
            )
            return

        try:
            LOGGER.info(
                "master.command_dispatch_start command=%s channel=%s user=%s text=%r",
                command_name,
                request.channel_id or "-",
                request.user_id or "-",
                request.text,
            )
            result = dispatch_slash_command(service, request)
            LOGGER.info(
                "master.command_dispatch_done command=%s channel=%s user=%s ok=%s code=%s",
                command_name,
                request.channel_id or "-",
                request.user_id or "-",
                result.ok,
                result.code,
            )
            respond(format_command_result(command_name, result))
        except ValueError as exc:
            LOGGER.warning(
                "master.command_rejected command=%s reason=invalid_args channel=%s user=%s error=%s text=%r",
                command_name,
                request.channel_id or "-",
                request.user_id or "-",
                str(exc),
                request.text,
            )
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


def create_master_app(
    *,
    bot_token: str,
    admin_channels: set[str],
    service: MasterService,
    router: ChannelRouter | None = None,
    rate_limiter: CommandRateLimiter | None = None,
) -> App:
    app = App(token=bot_token)
    LOGGER.info(
        "master.slack_app_init admin_channels=%s router_enabled=%s rate_limiter_enabled=%s",
        ",".join(sorted(admin_channels)),
        router is not None,
        rate_limiter is not None,
    )

    if router is not None:
        @app.event("app_mention")
        def on_mention(event: dict, say) -> None:  # type: ignore[no-untyped-def]
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts")
            event_ts = event.get("ts")
            text = event.get("text", "")
            user_id = event.get("user", "")

            try:
                router.track_thread(channel_id=channel_id, thread_ts=thread_ts)
                router.mark_mention_event(channel_id=channel_id, ts=event_ts)
                response = router.route_prompt(
                    channel_id=channel_id,
                    text=text,
                    thread_ts=thread_ts,
                    user_id=user_id,
                )
                say(text=response, thread_ts=thread_ts)
            except RouteSkip as exc:
                LOGGER.info("route skipped for channel=%s reason=%s", channel_id, exc)
            except RouteError as exc:
                LOGGER.warning("route dispatch failed for channel=%s error=%s", channel_id, exc)
                say(text=f"Error: {exc}", thread_ts=thread_ts)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("master mention handling failed")
                say(text=f"Error: {exc}", thread_ts=thread_ts)

        @app.event("message")
        def on_thread_message(event: dict, say) -> None:  # type: ignore[no-untyped-def]
            if event.get("subtype"):
                return

            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts")
            event_ts = event.get("ts")
            text = event.get("text", "")
            user_id = event.get("user", "")

            if not thread_ts or not text or not user_id:
                return
            if router.consume_marked_mention_event(channel_id=channel_id, ts=event_ts):
                return
            if not router.is_tracked_thread(channel_id=channel_id, thread_ts=thread_ts):
                return

            try:
                response = router.route_prompt(
                    channel_id=channel_id,
                    text=text,
                    thread_ts=thread_ts,
                    user_id=user_id,
                )
                say(text=response, thread_ts=thread_ts)
            except RouteSkip as exc:
                LOGGER.info("thread route skipped for channel=%s reason=%s", channel_id, exc)
            except RouteError as exc:
                LOGGER.warning("thread route dispatch failed for channel=%s error=%s", channel_id, exc)
                say(text=f"Error: {exc}", thread_ts=thread_ts)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("master thread handling failed")
                say(text=f"Error: {exc}", thread_ts=thread_ts)

    for command_name in (
        "/master-agent-list",
        "/master-agent-load",
        "/master-agent-start",
        "/master-agent-stop",
        "/master-agent-status",
        "/master-agent-remove",
        "/master-agent-refresh-auth",
    ):
        _register_command(
            app,
            command_name=command_name,
            admin_channels=admin_channels,
            service=service,
            rate_limiter=rate_limiter,
        )
        LOGGER.info("master.command_registered command=%s", command_name)

    return app
