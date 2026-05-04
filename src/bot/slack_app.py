from __future__ import annotations

import logging

from slack_bolt import App

from .service import AccessError, BotService

LOGGER = logging.getLogger(__name__)


def create_app(bot_token: str, service: BotService) -> App:
    app = App(token=bot_token)

    @app.event("app_mention")
    def on_mention(event: dict, say) -> None:  # type: ignore[no-untyped-def]
        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        event_ts = event.get("ts")
        text = event.get("text", "")
        user_id = event.get("user", "")

        try:
            service.track_thread(channel_id=channel_id, thread_ts=thread_ts)
            service.mark_mention_event(channel_id=channel_id, ts=event_ts)
            response = service.handle_prompt(
                channel_id=channel_id,
                text=text,
                thread_ts=thread_ts,
                user_id=user_id,
            )
            say(text=response, thread_ts=thread_ts)
            LOGGER.info(
                "slack.reply_sent channel_id=%s thread_ts=%s user_id=%s chars=%d",
                channel_id,
                thread_ts or "-",
                user_id or "-",
                len(response),
            )
        except AccessError:
            LOGGER.info("Ignored mention from non-allowlisted channel %s", channel_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to process mention")
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
        if service.consume_marked_mention_event(channel_id=channel_id, ts=event_ts):
            return
        if not service.is_tracked_thread(channel_id=channel_id, thread_ts=thread_ts):
            return

        try:
            response = service.handle_prompt(
                channel_id=channel_id,
                text=text,
                thread_ts=thread_ts,
                user_id=user_id,
            )
            say(text=response, thread_ts=thread_ts)
            LOGGER.info(
                "slack.thread_reply_sent channel_id=%s thread_ts=%s user_id=%s chars=%d",
                channel_id,
                thread_ts,
                user_id,
                len(response),
            )
        except AccessError:
            LOGGER.info("Ignored thread message from non-allowlisted channel %s", channel_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to process thread message")
            say(text=f"Error: {exc}", thread_ts=thread_ts)

    @app.command("/codex-status")
    def on_status(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()
        channel_id = command.get("channel_id", "")
        if not service.is_allowed_channel(channel_id):
            respond("This channel is not allowlisted.")
            return
        LOGGER.info("slash.codex-status channel_id=%s user_id=%s", channel_id, command.get("user_id", ""))
        respond(service.status_text())

    @app.command("/codex-attach")
    def on_attach(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()
        channel_id = command.get("channel_id", "")
        if not service.is_allowed_channel(channel_id):
            respond("This channel is not allowlisted.")
            return

        text = command.get("text", "").strip()
        if not text:
            respond("Usage: /codex-attach <session_id>")
            return

        try:
            LOGGER.info(
                "slash.codex-attach channel_id=%s user_id=%s session_id=%s",
                channel_id,
                command.get("user_id", ""),
                text,
            )
            respond(service.attach(text))
        except Exception as exc:  # noqa: BLE001
            respond(f"Attach failed: {exc}")

    @app.command("/codex-detach")
    def on_detach(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()
        channel_id = command.get("channel_id", "")
        if not service.is_allowed_channel(channel_id):
            respond("This channel is not allowlisted.")
            return
        LOGGER.info("slash.codex-detach channel_id=%s user_id=%s", channel_id, command.get("user_id", ""))
        respond(service.detach())

    @app.command("/codex-help")
    def on_help(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()
        channel_id = command.get("channel_id", "")
        if not service.is_allowed_channel(channel_id):
            respond("This channel is not allowlisted.")
            return
        LOGGER.info("slash.codex-help channel_id=%s user_id=%s", channel_id, command.get("user_id", ""))
        respond(
            "Use @codex <prompt> to chat. Commands: /codex-status, /codex-attach <session_id>, /codex-detach, /codex-conv-cancel, /codex-help"
        )

    @app.command("/codex-conv-cancel")
    def on_conv_cancel(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()
        channel_id = command.get("channel_id", "")
        if not service.is_allowed_channel(channel_id):
            respond("This channel is not allowlisted.")
            return
        LOGGER.info("slash.codex-conv-cancel channel_id=%s user_id=%s", channel_id, command.get("user_id", ""))
        respond(service.cancel_current_conversation())

    return app
