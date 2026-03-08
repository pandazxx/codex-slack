from __future__ import annotations

import asyncio
import logging
from typing import Any

from .command_runtime import execute_master_command
from .router import ChannelRouter, RouteError, RouteSkip
from .service import MasterService
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


def run_discord_frontend(
    *,
    bot_token: str,
    admin_channels: set[str],
    service: MasterService,
    router: ChannelRouter,
    rate_limiter: object | None = None,
) -> None:
    try:
        import discord
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("discord.py is required when MASTER_FRONTENDS includes discord") from exc

    intents = discord.Intents.default()
    intents.guilds = True
    intents.messages = True
    intents.message_content = True
    client = discord.Client(intents=intents)
    tree = discord.app_commands.CommandTree(client)

    async def _send_messages(interaction, messages: list[str]) -> None:  # type: ignore[no-untyped-def]
        if not messages:
            return
        if interaction.response.is_done():
            await interaction.followup.send(messages[0])
        else:
            await interaction.response.send_message(messages[0])
        for message in messages[1:]:
            await interaction.followup.send(message)

    def _run_command(*, command_name: str, text: str, channel_id: str, user_id: str) -> list[str]:
        return execute_master_command(
            command_name=command_name,
            text=text,
            channel_id=channel_id,
            user_id=user_id,
            admin_channels=admin_channels,
            service=service,
            router=router,
            rate_limiter=rate_limiter,  # type: ignore[arg-type]
        )

    @client.event
    async def on_ready() -> None:  # type: ignore[no-untyped-def]
        LOGGER.info("master.discord_ready user=%s", getattr(client.user, "id", "-"))
        await tree.sync()
        LOGGER.info("master.discord_commands_synced")

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

    @tree.command(name="master-agent-list", description="List all agents")
    async def cmd_list(interaction) -> None:  # type: ignore[no-untyped-def]
        messages = _run_command(
            command_name="/master-agent-list",
            text="",
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @tree.command(name="master-agent-load", description="Load an agent mapping")
    async def cmd_load(
        interaction,
        name: str,
        repo_path: str,
        channel_id: str,
        branch: str = "main",
        platform: str = "slack",
        adapter: str = "codex",
    ) -> None:  # type: ignore[no-untyped-def]
        text = f"{name} {repo_path} {channel_id} {branch} --platform {platform} --adapter {adapter}".strip()
        messages = _run_command(
            command_name="/master-agent-load",
            text=text,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @tree.command(name="master-agent-start", description="Start an agent")
    async def cmd_start(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        messages = _run_command(
            command_name="/master-agent-start",
            text=name,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @tree.command(name="master-agent-stop", description="Stop an agent")
    async def cmd_stop(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        messages = _run_command(
            command_name="/master-agent-stop",
            text=name,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @tree.command(name="master-agent-status", description="Show agent status")
    async def cmd_status(interaction, name: str, full: bool = False) -> None:  # type: ignore[no-untyped-def]
        text = f"{name} --full" if full else name
        messages = _run_command(
            command_name="/master-agent-status",
            text=text,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @tree.command(name="master-agent-usage", description="Show agent usage")
    async def cmd_usage(interaction, name: str = "") -> None:  # type: ignore[no-untyped-def]
        messages = _run_command(
            command_name="/master-agent-usage",
            text=name,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @tree.command(name="master-agent-remove", description="Remove an agent")
    async def cmd_remove(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        messages = _run_command(
            command_name="/master-agent-remove",
            text=name,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @tree.command(name="master-agent-refresh-auth", description="Refresh agent auth in workspace")
    async def cmd_refresh_auth(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        messages = _run_command(
            command_name="/master-agent-refresh-auth",
            text=name,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    client.run(bot_token)
