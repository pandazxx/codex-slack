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
        text = event.get("text", "")

        try:
            response = service.handle_prompt(channel_id=channel_id, text=text)
            say(text=response, thread_ts=thread_ts)
        except AccessError:
            LOGGER.info("Ignored mention from non-allowlisted channel %s", channel_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to process mention")
            say(text=f"Error: {exc}", thread_ts=thread_ts)

    @app.command("/codex-status")
    def on_status(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()
        channel_id = command.get("channel_id", "")
        if not service.is_allowed_channel(channel_id):
            respond("This channel is not allowlisted.")
            return
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
        respond(service.detach())

    @app.command("/codex-help")
    def on_help(ack, respond, command: dict) -> None:  # type: ignore[no-untyped-def]
        ack()
        channel_id = command.get("channel_id", "")
        if not service.is_allowed_channel(channel_id):
            respond("This channel is not allowlisted.")
            return
        respond(
            "Use @codex <prompt> to chat. Commands: /codex-status, /codex-attach <session_id>, /codex-detach, /codex-help"
        )

    return app
