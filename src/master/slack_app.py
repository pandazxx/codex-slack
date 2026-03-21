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


def _clip(value: object, limit: int = 40) -> str:
    text = str(value) if value is not None else "-"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_agent_list_table(result: CommandResult) -> str | None:
    agents = result.data.get("agents")
    if not isinstance(agents, list):
        return None

    if not agents:
        return ":white_check_mark: *No agents loaded.*"

    lines = []
    for item in agents:
        if not isinstance(item, dict):
            continue
        lines.append(
            (
                f"• *{_clip(item.get('name', '-'), 24)}*"
                f" | state=`{_clip(item.get('status', '-'), 12)}`"
                f" | channel=`{_clip(item.get('channel_id', '-'), 14)}`"
                f" | ref=`{_clip(item.get('repo_ref', '-'), 12)}`"
                f" | runtime=`{_clip(item.get('runtime', '-'), 10)}`"
                f" | container=`{_clip(item.get('container_name', '-'), 28)}`"
            )
        )

    return "\n".join(
        [
            f":white_check_mark: */master-agent-list*",
            f"*Total agents:* {len(agents)}",
            *lines,
        ]
    )


def _format_agent_usage(result: CommandResult) -> str | None:
    usage = result.data.get("usage")
    if not isinstance(usage, list):
        return None
    if not usage:
        return ":white_check_mark: *No usage recorded yet.*"

    lines = [":bar_chart: */master-agent-usage*"]
    for item in usage:
        if not isinstance(item, dict):
            continue
        lines.append(
            (
                f"• *{_clip(item.get('agent_name', '-'), 24)}*"
                f" | prompts=`{item.get('prompt_count', 0)}`"
                f" | images=`{item.get('image_count', 0)}`"
                f" | prompt_chars=`{item.get('prompt_chars', 0)}`"
                f" | response_chars=`{item.get('response_chars', 0)}`"
                f" | avg_ms=`{item.get('avg_latency_ms', 0)}`"
            )
        )
    return "\n".join(lines)


def format_command_result(command_name: str, result: CommandResult) -> str:
    if command_name == "/master-agent-list":
        rendered_table = _format_agent_list_table(result)
        if rendered_table:
            return rendered_table
    if command_name == "/master-agent-usage":
        rendered_usage = _format_agent_usage(result)
        if rendered_usage:
            return rendered_usage
    if command_name == "/master-agent-status":
        rendered_summary = _format_status_summary(result)
        if rendered_summary:
            return rendered_summary

    status_icon = ":white_check_mark:" if result.ok else ":x:"
    lines = [
        f"{status_icon} *{command_name}*",
        f"*Code:* `{result.code}`",
        f"*Message:* {result.message}",
    ]
    if result.data:
        data_json = json.dumps(result.data, indent=2, sort_keys=True)
        if len(data_json) > 6000:
            data_json = data_json[:5997] + "..."
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


def parse_optional_name_text(text: str) -> str | None:
    value = text.strip()
    return value or None


def parse_set_model_text(text: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in text.split() if part.strip()]
    if len(parts) == 1:
        return parts[0], None  # clear model
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError("usage: /master-agent-set-model <name> [model]")


def is_supported_thread_subtype(subtype: str | None) -> bool:
    if not subtype:
        return True
    return subtype in {"file_share"}


def parse_status_text(text: str, command_name: str = "/master-agent-status") -> tuple[str, bool]:
    parts = [part.strip() for part in text.split() if part.strip()]
    if not parts:
        raise ValueError(f"usage: {command_name} <name> [--full]")
    if len(parts) == 1:
        return parts[0], False
    if len(parts) == 2 and parts[1] == "--full":
        return parts[0], True
    raise ValueError(f"usage: {command_name} <name> [--full]")


def wants_full_status(command_name: str, text: str) -> bool:
    if command_name != "/master-agent-status":
        return False
    try:
        _, is_full = parse_status_text(text, command_name)
        return is_full
    except ValueError:
        return False


def _chunk_text(text: str, max_chars: int = 2800) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_chars and current:
            chunks.append(current)
            current = ""
        if len(line) > max_chars:
            for i in range(0, len(line), max_chars):
                part = line[i : i + max_chars]
                if len(part) == max_chars:
                    chunks.append(part)
                else:
                    current = part
            continue
        current += line
    if current:
        chunks.append(current)
    return chunks


def format_status_full_chunks(command_name: str, result: CommandResult) -> list[str]:
    payload = {
        "ok": result.ok,
        "command": command_name,
        "code": result.code,
        "message": result.message,
        "data": result.data,
    }
    raw = json.dumps(payload, indent=2, sort_keys=True)
    parts = _chunk_text(raw, max_chars=2800)
    total = len(parts)
    return [
        f":information_source: *{command_name} full output* (part {idx}/{total})\n```json\n{part}\n```"
        for idx, part in enumerate(parts, start=1)
    ]


def _format_status_summary(result: CommandResult) -> str | None:
    if not isinstance(result.data, dict):
        return None
    record = result.data.get("record")
    runtime = result.data.get("runtime")
    if not isinstance(record, dict):
        return None

    runtime_state = "-"
    if isinstance(runtime, dict):
        state = runtime.get("State")
        if isinstance(state, dict):
            runtime_state = str(state.get("Status") or state.get("Running") or "-")

    return "\n".join(
        [
            ":white_check_mark: */master-agent-status*",
            f"*Agent:* `{record.get('name', '-')}`",
            f"*State:* registry=`{record.get('status', '-')}` runtime=`{runtime_state}`",
            f"*Channel:* `{record.get('channel_id', '-')}`",
            f"*Branch:* `{record.get('repo_ref', '-')}`",
            f"*Container:* `{record.get('container_name', '-')}`",
            f"*Image:* `{record.get('resolved_image') or '-'}`",
            f"*Last error:* `{record.get('last_error') or '-'}`",
            "_Use `/master-agent-status <name> --full` for full JSON output._",
        ]
    )


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
        name, _ = parse_status_text(request.text)
        return service.status(name=name)

    if request.command_name == "/master-agent-remove":
        name = parse_single_name_text(request.text, request.command_name)
        return service.remove_agent(name=name)

    if request.command_name == "/master-agent-refresh-auth":
        name = parse_single_name_text(request.text, request.command_name)
        return service.refresh_agent_auth(name=name)

    if request.command_name == "/master-agent-set-model":
        name, model = parse_set_model_text(request.text)
        return service.set_agent_model(name=name, model=model)

    return CommandResult(ok=False, code="ERR_INVALID_ARGS", message=f"unsupported command: {request.command_name}", data={})


def _register_command(
    app: App,
    *,
    command_name: str,
    admin_channels: set[str],
    service: MasterService,
    router: ChannelRouter | None = None,
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
            if command_name == "/master-agent-usage":
                if router is None:
                    result = CommandResult(
                        ok=False,
                        code="ERR_INTERNAL",
                        message="usage router is not configured",
                        data={},
                    )
                else:
                    selected_agent = parse_optional_name_text(request.text)
                    result = CommandResult(
                        ok=True,
                        code="OK",
                        message="usage listed",
                        data={"usage": router.usage_summary(selected_agent)},
                    )
            else:
                result = dispatch_slash_command(service, request)

            LOGGER.info(
                "master.command_dispatch_start command=%s channel=%s user=%s text=%r",
                command_name,
                request.channel_id or "-",
                request.user_id or "-",
                request.text,
            )
            LOGGER.info(
                "master.command_dispatch_done command=%s channel=%s user=%s ok=%s code=%s",
                command_name,
                request.channel_id or "-",
                request.user_id or "-",
                result.ok,
                result.code,
            )
            if command_name == "/master-agent-status" and result.ok and parse_status_text(request.text)[1]:
                for message in format_status_full_chunks(command_name, result):
                    respond(message)
            else:
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
            image_urls = extract_image_urls(event.get("files", []), event_ts)

            try:
                router.track_thread(channel_id=channel_id, thread_ts=thread_ts)
                router.mark_mention_event(channel_id=channel_id, ts=event_ts)
                say(text=format_forward_ack(text=text, image_count=len(image_urls)), thread_ts=thread_ts)
                response = router.route_prompt(
                    channel_id=channel_id,
                    text=text,
                    thread_ts=thread_ts,
                    user_id=user_id,
                    image_urls=image_urls,
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
            subtype = event.get("subtype")
            if not is_supported_thread_subtype(subtype):
                LOGGER.info(
                    "thread message ignored: unsupported subtype channel=%s thread_ts=%s subtype=%s",
                    event.get("channel", "-"),
                    event.get("thread_ts", "-"),
                    subtype,
                )
                return

            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts")
            event_ts = event.get("ts")
            text = event.get("text", "")
            user_id = event.get("user", "")
            image_urls = extract_image_urls(event.get("files", []), event_ts)
            image_urls = select_thread_image_urls(image_urls, subtype)
            if image_urls:
                LOGGER.info(
                    "thread image payload channel=%s thread_ts=%s subtype=%s image_count=%d first_image=%s",
                    channel_id or "-",
                    thread_ts or "-",
                    subtype or "-",
                    len(image_urls),
                    image_urls[0],
                )

            if not thread_ts or not user_id or (not text and not image_urls):
                LOGGER.info(
                    "thread message ignored: missing payload channel=%s thread_ts=%s user=%s text_chars=%d image_count=%d",
                    channel_id or "-",
                    thread_ts or "-",
                    user_id or "-",
                    len(text),
                    len(image_urls),
                )
                return
            if router.consume_marked_mention_event(channel_id=channel_id, ts=event_ts):
                LOGGER.info(
                    "thread message ignored: deduped mention event channel=%s ts=%s",
                    channel_id,
                    event_ts or "-",
                )
                return
            if not router.is_tracked_thread(channel_id=channel_id, thread_ts=thread_ts):
                LOGGER.info(
                    "thread message ignored: untracked thread channel=%s thread_ts=%s image_count=%d",
                    channel_id,
                    thread_ts,
                    len(image_urls),
                )
                return

            try:
                say(text=format_forward_ack(text=text, image_count=len(image_urls)), thread_ts=thread_ts)
                LOGGER.info(
                    "thread route dispatch_start channel=%s thread_ts=%s user=%s text_chars=%d image_count=%d subtype=%s",
                    channel_id,
                    thread_ts,
                    user_id,
                    len(text),
                    len(image_urls),
                    subtype or "-",
                )
                response = router.route_prompt(
                    channel_id=channel_id,
                    text=text,
                    thread_ts=thread_ts,
                    user_id=user_id,
                    image_urls=image_urls,
                )
                say(text=response, thread_ts=thread_ts)
                LOGGER.info(
                    "thread route dispatch_done channel=%s thread_ts=%s user=%s response_chars=%d",
                    channel_id,
                    thread_ts,
                    user_id,
                    len(response),
                )
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
        "/master-agent-usage",
        "/master-agent-remove",
        "/master-agent-refresh-auth",
        "/master-agent-set-model",
    ):
        _register_command(
            app,
            command_name=command_name,
            admin_channels=admin_channels,
            service=service,
            router=router,
            rate_limiter=rate_limiter,
        )
        LOGGER.info("master.command_registered command=%s", command_name)

    return app


def _file_matches_event_ts(file_item: dict, event_ts: str | None) -> bool:
    if not event_ts:
        return True
    shares = file_item.get("shares")
    if not isinstance(shares, dict):
        return True

    for scope in ("public", "private"):
        channels = shares.get(scope)
        if not isinstance(channels, dict):
            continue
        for entries in channels.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("ts", "")) == event_ts:
                    return True
    return False


def extract_image_urls(files: object, event_ts: str | None = None) -> list[str]:
    if not isinstance(files, list):
        return []
    urls: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        if not _file_matches_event_ts(item, event_ts):
            continue
        mimetype = str(item.get("mimetype", ""))
        if not mimetype.startswith("image/"):
            continue
        url = str(item.get("url_private_download") or item.get("url_private") or "").strip()
        if url:
            urls.append(url)
    return urls


def select_thread_image_urls(image_urls: list[str], subtype: str | None) -> list[str]:
    # Keep all image URLs from the current message payload.
    # Historical thread files are filtered earlier by event timestamp matching.
    if subtype == "file_share" and image_urls:
        return image_urls
    return image_urls


def format_forward_ack(*, text: str, image_count: int) -> str:
    text_chars = len(text)
    return (
        ":incoming_envelope: Received message and forwarded to agent. "
        f"text_chars=`{text_chars}` images=`{image_count}`"
    )
