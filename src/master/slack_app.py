from __future__ import annotations

from dataclasses import dataclass, field
import logging
from threading import Lock
import time

from slack_bolt import App

from .command_dispatch import (
    MasterCommandRequest as SlackCommandRequest,
    dispatch_command as dispatch_slash_command,
    parse_load_text,
    parse_status_text,
)
from .command_format import (
    format_command_result,
    format_status_full_chunks,
    wants_full_status,
)
from .command_runtime import execute_master_command
from .command_runtime import is_admin_channel
from .dispatch_guard import in_flight_dispatch, is_shutting_down
from .router import ChannelRouter, RouteError, RouteSkip, RoutedAttachment, make_image_attachments
from .service import CommandResult, MasterService

LOGGER = logging.getLogger(__name__)
_DOCUMENT_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/pdf": "pdf",
}
_FORMAT_CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


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


def is_supported_thread_subtype(subtype: str | None) -> bool:
    if not subtype:
        return True
    return subtype in {"file_share"}


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

        text = command.get("text", "")
        channel_id = command.get("channel_id", "")
        user_id = command.get("user_id", "")
        messages = execute_master_command(
            platform="slack",
            command_name=command_name,
            text=text,
            channel_id=channel_id,
            user_id=user_id,
            admin_channels=admin_channels,
            service=service,
            router=router,
            rate_limiter=rate_limiter,
        )
        for message in messages:
            respond(message)


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
        def on_mention(event: dict, say, client) -> None:  # type: ignore[no-untyped-def]
            if is_shutting_down():
                LOGGER.info("master.shutting_down dropping mention")
                return
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts")
            event_ts = event.get("ts")
            text = event.get("text", "")
            user_id = event.get("user", "")
            image_urls = extract_image_urls(event.get("files", []), event_ts)
            attachments = extract_routed_attachments(event.get("files", []), event_ts)

            try:
                say(text=format_forward_ack(text=text, image_count=len(image_urls)), thread_ts=thread_ts)
                client.reactions_add(channel=channel_id, timestamp=event_ts, name="hourglass_flowing_sand")
                try:
                    with in_flight_dispatch():
                        response = router.route_mention_message(
                            platform="slack",
                            channel_id=channel_id,
                            text=text,
                            thread_ts=thread_ts,
                            event_ts=event_ts,
                            user_id=user_id,
                            image_urls=image_urls,
                            attachments=attachments,
                        )
                    say(text=response, thread_ts=thread_ts)
                finally:
                    client.reactions_remove(channel=channel_id, timestamp=event_ts, name="hourglass_flowing_sand")
            except RouteSkip as exc:
                LOGGER.info("route skipped for channel=%s reason=%s", channel_id, exc)
            except RouteError as exc:
                LOGGER.warning("route dispatch failed for channel=%s error=%s", channel_id, exc)
                say(text=f"Error: {exc}", thread_ts=thread_ts)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("master mention handling failed")
                say(text=f"Error: {exc}", thread_ts=thread_ts)

        @app.event("message")
        def on_thread_message(event: dict, say, client) -> None:  # type: ignore[no-untyped-def]
            if is_shutting_down():
                LOGGER.info("master.shutting_down dropping thread message")
                return
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
            attachments = extract_routed_attachments(event.get("files", []), event_ts)
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

            try:
                LOGGER.info(
                    "thread route dispatch_start channel=%s thread_ts=%s user=%s text_chars=%d image_count=%d subtype=%s",
                    channel_id,
                    thread_ts,
                    user_id,
                    len(text),
                    len(image_urls),
                    subtype or "-",
                )
                should_route = router.accept_followup_message(
                    platform="slack",
                    channel_id=channel_id,
                    text=text,
                    thread_ts=thread_ts,
                    event_ts=event_ts,
                    user_id=user_id,
                    image_urls=image_urls,
                    attachments=attachments,
                )
                if not should_route:
                    LOGGER.info(
                        "thread message ignored: untracked_or_deduped channel=%s thread_ts=%s image_count=%d",
                        channel_id,
                        thread_ts,
                        len(image_urls),
                    )
                    return
                say(text=format_forward_ack(text=text, image_count=len(image_urls)), thread_ts=thread_ts)
                client.reactions_add(channel=channel_id, timestamp=event_ts, name="hourglass_flowing_sand")
                try:
                    with in_flight_dispatch():
                        response = router.route_prompt(
                            platform="slack",
                            channel_id=channel_id,
                            text=text,
                            thread_ts=thread_ts,
                            user_id=user_id,
                            image_urls=image_urls,
                            attachments=attachments,
                        )
                    say(text=response, thread_ts=thread_ts)
                    LOGGER.info(
                        "thread route dispatch_done channel=%s thread_ts=%s user=%s response_chars=%d",
                        channel_id,
                        thread_ts,
                        user_id,
                        len(response),
                    )
                finally:
                    client.reactions_remove(channel=channel_id, timestamp=event_ts, name="hourglass_flowing_sand")
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
        "/master-agent-refresh-config",
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
    if subtype == "file_share":
        return list(image_urls)
    return list(image_urls[:1])


def extract_document_attachments(files: object, event_ts: str | None = None) -> list[RoutedAttachment]:
    if not isinstance(files, list):
        return []
    attachments: list[RoutedAttachment] = []
    for idx, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            continue
        if not _file_matches_event_ts(item, event_ts):
            continue
        mimetype = str(item.get("mimetype", "")).strip()
        filename = str(item.get("name", "") or f"document-{idx}").strip()
        format_hint = _DOCUMENT_MIME_TYPES.get(mimetype)
        if not format_hint:
            lowered = filename.lower()
            if lowered.endswith(".docx"):
                format_hint = "docx"
            elif lowered.endswith(".pdf"):
                format_hint = "pdf"
        if not format_hint:
            continue
        url = str(item.get("url_private_download") or item.get("url_private") or "").strip()
        if not url:
            continue
        attachments.append(
            RoutedAttachment(
                id=str(item.get("id") or f"doc-{idx}"),
                kind="document",
                filename=filename,
                content_type=mimetype or _FORMAT_CONTENT_TYPES.get(format_hint, "application/octet-stream"),
                source_url=url,
                format_hint=format_hint,
            )
        )
    return attachments


def extract_routed_attachments(files: object, event_ts: str | None = None) -> list[RoutedAttachment]:
    return extract_document_attachments(files, event_ts) + make_image_attachments(extract_image_urls(files, event_ts))


def format_forward_ack(*, text: str, image_count: int) -> str:
    text_chars = len(text)
    return (
        ":incoming_envelope: Received message and forwarded to agent. "
        f"text_chars=`{text_chars}` images=`{image_count}`"
    )
