from __future__ import annotations

import asyncio
import logging
from typing import Any

from .router import ChannelRouter, RouteError, RouteSkip
from .slack_app import format_forward_ack

LOGGER = logging.getLogger(__name__)


def _extract_image_urls(attachments: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in attachments:
        content_type = str(getattr(item, "content_type", "") or "")
        if not content_type.startswith("image/"):
            continue
        url = str(getattr(item, "url", "") or "").strip()
        if url:
            urls.append(url)
    return urls


def run_discord_frontend(*, bot_token: str, router: ChannelRouter) -> None:
    try:
        import discord
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("discord.py is required when MASTER_FRONTENDS includes discord") from exc

    intents = discord.Intents.default()
    intents.guilds = True
    intents.messages = True
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:  # type: ignore[no-untyped-def]
        LOGGER.info("master.discord_ready user=%s", getattr(client.user, "id", "-"))

    @client.event
    async def on_message(message) -> None:  # type: ignore[no-untyped-def]
        if message.author.bot:
            return

        channel_id = str(message.channel.id)
        event_ts = str(message.id)
        text = str(message.content or "")
        user_id = str(message.author.id)
        image_urls = _extract_image_urls(list(message.attachments))
        is_mention = client.user is not None and client.user.mentioned_in(message)

        reference = getattr(message, "reference", None)
        thread_ts = str(getattr(reference, "message_id", "") or message.id)

        try:
            if is_mention:
                say_thread = thread_ts
                say_text = format_forward_ack(text=text, image_count=len(image_urls))
                await message.reply(say_text, mention_author=False)
                response = await asyncio.to_thread(
                    router.route_mention_message,
                    platform="discord",
                    channel_id=channel_id,
                    text=text,
                    thread_ts=say_thread,
                    event_ts=event_ts,
                    user_id=user_id,
                    image_urls=image_urls,
                )
                await message.reply(response, mention_author=False)
                return

            response = await asyncio.to_thread(
                router.route_followup_message,
                platform="discord",
                channel_id=channel_id,
                text=text,
                thread_ts=thread_ts,
                event_ts=event_ts,
                user_id=user_id,
                image_urls=image_urls,
            )
            if response is None:
                return
            await message.reply(format_forward_ack(text=text, image_count=len(image_urls)), mention_author=False)
            await message.reply(response, mention_author=False)
        except RouteSkip as exc:
            LOGGER.info("discord route skipped for channel=%s reason=%s", channel_id, exc)
        except RouteError as exc:
            LOGGER.warning("discord route failed for channel=%s error=%s", channel_id, exc)
            await message.reply(f"Error: {exc}", mention_author=False)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("master discord message handling failed")
            await message.reply(f"Error: {exc}", mention_author=False)

    client.run(bot_token)
