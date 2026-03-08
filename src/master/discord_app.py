from __future__ import annotations

import asyncio
import logging
from typing import Any

from .router import ChannelRouter, RouteError, RouteSkip
from .slack_app import format_forward_ack

LOGGER = logging.getLogger(__name__)


def _extract_image_urls(attachments: list[Any]) -> list[str]:
    urls: list[str] = []
    for attachment in attachments:
        content_type = str(getattr(attachment, "content_type", "") or "")
        if not content_type.startswith("image/"):
            continue
        url = str(getattr(attachment, "url", "") or "").strip()
        if url:
            urls.append(url)
    return urls


def run_discord_frontend(*, bot_token: str, router: ChannelRouter) -> None:
    try:
        import discord
    except ImportError as exc:  # pragma: no cover - runtime dependency check
        raise RuntimeError("discord.py is required for MASTER_FRONTEND=discord") from exc

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:  # type: ignore[no-untyped-def]
        user_id = str(getattr(client.user, "id", "-"))
        LOGGER.info("master.discord_ready user_id=%s", user_id)

    @client.event
    async def on_message(message) -> None:  # type: ignore[no-untyped-def]
        if message.author.bot:
            return
        if client.user is None or not client.user.mentioned_in(message):
            return

        channel_id = str(message.channel.id)
        thread_ts = str(message.id)
        event_ts = str(message.id)
        text = str(message.content or "")
        user_id = str(message.author.id)
        image_urls = _extract_image_urls(list(message.attachments))

        try:
            router.track_thread(channel_id=channel_id, thread_ts=thread_ts)
            router.mark_mention_event(channel_id=channel_id, ts=event_ts)
            await message.reply(
                format_forward_ack(text=text, image_count=len(image_urls)),
                mention_author=False,
            )
            response = await asyncio.to_thread(
                router.route_prompt,
                channel_id=channel_id,
                text=text,
                thread_ts=thread_ts,
                user_id=user_id,
                image_urls=image_urls,
            )
            await message.reply(response, mention_author=False)
        except RouteSkip as exc:
            LOGGER.info("discord route skipped for channel=%s reason=%s", channel_id, exc)
        except RouteError as exc:
            LOGGER.warning("discord route dispatch failed for channel=%s error=%s", channel_id, exc)
            await message.reply(f"Error: {exc}", mention_author=False)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("master discord handling failed")
            await message.reply(f"Error: {exc}", mention_author=False)

    client.run(bot_token)
