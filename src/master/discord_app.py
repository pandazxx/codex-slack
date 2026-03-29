from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
from typing import Any
from urllib.request import Request, urlopen

from .command_runtime import execute_master_command
from .dispatch_guard import in_flight_dispatch, is_shutting_down
from .router import ChannelRouter, RouteError, RouteSkip, RoutedAttachment
from .service import MasterService
from .slack_app import format_forward_ack

LOGGER = logging.getLogger(__name__)
DISCORD_COMMAND_PATTERN = re.compile(r"^<@!?\d+>\s*")
DISCORD_MESSAGE_LIMIT = 1900
DISCORD_FILE_THRESHOLD = 8000  # send as file attachment above this length

_MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_MD_TABLE_ROW = re.compile(r"^\|.+\|$")
_MD_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|$")


_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_EXTENSIONS = {".txt", ".log", ".md", ".json", ".yaml", ".yml", ".toml", ".sh", ".py", ".js", ".ts", ".csv"}
_TEXT_ATTACHMENT_SIZE_LIMIT = 512 * 1024  # 512 KB
_DOCUMENT_EXTENSIONS = {".docx": "docx", ".pdf": "pdf"}
_DOCUMENT_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/pdf": "pdf",
}


def preprocess_for_discord(text: str) -> str:
    """Convert agent markdown to Discord-compatible format.

    Replaces # headers (not rendered by Discord) with bold text and strips
    horizontal rules which render as literal dashes.
    """
    lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            lines.append(f"**{m.group(2).strip()}**")
            continue
        if re.match(r"^-{3,}$", line.strip()):
            continue
        lines.append(line)
    return "\n".join(lines)


def _convert_markdown_tables(text: str) -> str:
    """Convert markdown pipe tables to fixed-width monospace code blocks.

    Tables already inside a code block are left untouched to avoid
    double-nesting fences.
    """
    lines = text.splitlines()
    result: list[str] = []
    i = 0
    in_code_block = False
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(lines[i])
            i += 1
            continue
        if not in_code_block and _MD_TABLE_ROW.match(lines[i].strip()):
            table_lines: list[str] = [lines[i]]
            j = i + 1
            while j < len(lines) and _MD_TABLE_ROW.match(lines[j].strip()):
                table_lines.append(lines[j])
                j += 1
            # Require at least header + separator + one data row, with a valid separator
            if len(table_lines) >= 3 and _MD_TABLE_SEP.match(table_lines[1].strip()):
                rows = [
                    [cell.strip() for cell in row.strip().strip("|").split("|")]
                    for row in table_lines
                    if not _MD_TABLE_SEP.match(row.strip())
                ]
                if rows:
                    col_count = max(len(row) for row in rows)
                    rows = [row + [""] * (col_count - len(row)) for row in rows]
                    widths = [max(len(row[c]) for row in rows) for c in range(col_count)]
                    formatted: list[str] = []
                    for ridx, row in enumerate(rows):
                        formatted.append("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)).rstrip())
                        if ridx == 0:
                            formatted.append("  ".join("-" * widths[c] for c in range(col_count)))
                    result.append("```\n" + "\n".join(formatted) + "\n```")
                    i = j
                    continue
        result.append(lines[i])
        i += 1
    return "\n".join(result)


def _fetch_mermaid_image(diagram: str) -> bytes | None:
    """Render a mermaid diagram to PNG via mermaid.ink. Returns PNG bytes or None on failure."""
    encoded = base64.urlsafe_b64encode(diagram.encode()).decode().rstrip("=")
    url = f"https://mermaid.ink/img/{encoded}"
    try:
        req = Request(url, headers={"User-Agent": "codex-slack/1.0"})
        with urlopen(req, timeout=10) as resp:
            return bytes(resp.read())
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("discord.mermaid_render_failed url=%s error=%s", url, exc)
        return None


async def _render_mermaid_blocks(text: str) -> tuple[str, list[io.BytesIO]]:
    """Extract mermaid code blocks, render via mermaid.ink, return (cleaned_text, png_buffers).

    Blocks that fail to render are left unchanged in the text.
    """
    matches = list(_MERMAID_FENCE.finditer(text))
    if not matches:
        return text, []

    buffers: list[io.BytesIO] = []
    replacements: list[tuple[int, int]] = []

    for match in matches:
        diagram = match.group(1).strip()
        png = await asyncio.to_thread(_fetch_mermaid_image, diagram)
        if png is not None:
            buffers.append(io.BytesIO(png))
            replacements.append((match.start(), match.end()))

    result = text
    for start, end in reversed(replacements):
        result = result[:start] + result[end:]

    return result.strip(), buffers


def _is_text_attachment(attachment: Any) -> bool:
    content_type = str(getattr(attachment, "content_type", "") or "").split(";")[0].strip()
    if any(content_type.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    filename = str(getattr(attachment, "filename", "") or "")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in _TEXT_EXTENSIONS


async def _read_text_attachments(attachments: list[Any]) -> str:
    """Download text/file attachments and return their contents joined as a string."""
    parts: list[str] = []
    for attachment in attachments:
        if not _is_text_attachment(attachment):
            continue
        size = getattr(attachment, "size", 0) or 0
        if size > _TEXT_ATTACHMENT_SIZE_LIMIT:
            parts.append(f"[attachment {attachment.filename}: too large to include ({size:,} bytes)]")
            continue
        try:
            raw = await attachment.read()
            text = raw.decode("utf-8", errors="replace")
            parts.append(f"[attachment: {attachment.filename}]\n{text}")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("discord.text_attachment_read_failed filename=%s error=%s", getattr(attachment, "filename", "?"), exc)
    return "\n\n".join(parts)


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


def _is_document_attachment(attachment: Any) -> bool:
    content_type = str(getattr(attachment, "content_type", "") or "").split(";")[0].strip()
    if content_type in _DOCUMENT_MIME_TYPES:
        return True
    filename = str(getattr(attachment, "filename", "") or "")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in _DOCUMENT_EXTENSIONS


def _extract_document_attachments(attachments: list[Any]) -> list[RoutedAttachment]:
    results: list[RoutedAttachment] = []
    for idx, item in enumerate(attachments, start=1):
        if not _is_document_attachment(item):
            continue
        filename = str(getattr(item, "filename", "") or f"document-{idx}")
        content_type = str(getattr(item, "content_type", "") or "").split(";")[0].strip()
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        format_hint = _DOCUMENT_MIME_TYPES.get(content_type) or _DOCUMENT_EXTENSIONS.get(ext)
        url = str(getattr(item, "url", "") or "").strip()
        if not format_hint or not url:
            continue
        results.append(
            RoutedAttachment(
                id=str(getattr(item, "id", f"doc-{idx}")),
                kind="document",
                filename=filename,
                content_type=content_type or "application/octet-stream",
                source_url=url,
                format_hint=format_hint,
            )
        )
    return results


def _extract_routed_attachments(attachments: list[Any]) -> list[RoutedAttachment]:
    image_attachments: list[RoutedAttachment] = []
    for idx, url in enumerate(_extract_image_urls(attachments), start=1):
        image_attachments.append(
            RoutedAttachment(
                id=f"img-{idx}",
                kind="image",
                filename=f"image-{idx}",
                content_type="image/*",
                source_url=url,
            )
        )
    return _extract_document_attachments(attachments) + image_attachments


def parse_admin_message_command(text: str) -> tuple[str, str] | None:
    stripped = DISCORD_COMMAND_PATTERN.sub("", text.strip())
    if not stripped.startswith("/master-agent-"):
        return None
    parts = stripped.split(maxsplit=1)
    command_name = parts[0]
    args_text = parts[1] if len(parts) > 1 else ""
    return command_name, args_text


def split_discord_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            # No newline found — split at last space to avoid mid-word cuts.
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n").lstrip(" ")
    if remaining:
        chunks.append(remaining)
    return [chunk for chunk in chunks if chunk]


def label_discord_chunks(chunks: list[str]) -> list[str]:
    total = len(chunks)
    if total <= 1:
        return list(chunks)
    return [f"[{idx}/{total}]\n{chunk}" for idx, chunk in enumerate(chunks, start=1)]


def _make_file(text: str, filename: str = "response.md"):  # type: ignore[no-untyped-def]
    return io.BytesIO(text.encode("utf-8")), filename



async def sync_registered_commands(*, tree, client, admin_channels: set[str], discord_module) -> None:  # type: ignore[no-untyped-def]
    guild_ids: set[int] = set()
    for raw_channel_id in admin_channels:
        try:
            channel_id = int(raw_channel_id)
        except ValueError:
            LOGGER.warning("master.discord_admin_channel_invalid channel_id=%s", raw_channel_id)
            continue

        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "master.discord_admin_channel_lookup_failed channel_id=%s error=%s",
                    raw_channel_id,
                    exc,
                )
                continue

        guild = getattr(channel, "guild", None)
        guild_id = getattr(guild, "id", None)
        if guild_id is None:
            LOGGER.warning("master.discord_admin_channel_missing_guild channel_id=%s", raw_channel_id)
            continue
        guild_ids.add(int(guild_id))

    for guild_id in sorted(guild_ids):
        guild = discord_module.Object(id=guild_id)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        LOGGER.info("master.discord_commands_synced guild_id=%s scope=guild", guild_id)

    await tree.sync()
    LOGGER.info("master.discord_commands_synced scope=global")


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
        expanded: list[str] = []
        for message in messages:
            expanded.extend(split_discord_message(message))
        if not expanded:
            return
        first, rest = expanded[0], expanded[1:]
        if interaction.response.is_done():
            await interaction.followup.send(first)
        else:
            await interaction.response.send_message(first)
        for msg in rest:
            await interaction.followup.send(msg)

    async def _reply_message_chunks(message, text: str) -> None:  # type: ignore[no-untyped-def]
        text = preprocess_for_discord(text)
        text = _convert_markdown_tables(text)
        text, mermaid_buffers = await _render_mermaid_blocks(text)
        if len(text) > DISCORD_FILE_THRESHOLD:
            buf, fname = _make_file(text)
            await message.reply(
                f"Response is too long ({len(text):,} chars) — sending as file.",
                file=discord.File(buf, filename=fname),
                mention_author=False,
            )
        else:
            for chunk in split_discord_message(text):
                await message.reply(chunk, mention_author=False)
        for idx, buf in enumerate(mermaid_buffers, start=1):
            await message.reply(file=discord.File(buf, filename=f"diagram-{idx}.png"), mention_author=False)

    async def _reply_in_thread(thread, text: str) -> None:  # type: ignore[no-untyped-def]
        """Send a message directly into a thread channel (no reply reference)."""
        text = preprocess_for_discord(text)
        text = _convert_markdown_tables(text)
        text, mermaid_buffers = await _render_mermaid_blocks(text)
        if len(text) > DISCORD_FILE_THRESHOLD:
            buf, fname = _make_file(text)
            await thread.send(
                f"Response is too long ({len(text):,} chars) — sending as file.",
                file=discord.File(buf, filename=fname),
            )
        else:
            for chunk in split_discord_message(text):
                await thread.send(chunk)
        for idx, buf in enumerate(mermaid_buffers, start=1):
            await thread.send(file=discord.File(buf, filename=f"diagram-{idx}.png"))

    def _run_command(*, command_name: str, text: str, channel_id: str, user_id: str) -> list[str]:
        return execute_master_command(
            platform="discord",
            command_name=command_name,
            text=text,
            channel_id=channel_id,
            user_id=user_id,
            admin_channels=admin_channels,
            service=service,
            router=router,
            rate_limiter=rate_limiter,  # type: ignore[arg-type]
        )

    async def _execute_and_send(interaction, *, command_name: str, text: str) -> None:  # type: ignore[no-untyped-def]
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
        messages = await asyncio.to_thread(
            _run_command,
            command_name=command_name,
            text=text,
            channel_id=str(interaction.channel_id),
            user_id=str(interaction.user.id),
        )
        await _send_messages(interaction, messages)

    @client.event
    async def on_ready() -> None:  # type: ignore[no-untyped-def]
        LOGGER.info("master.discord_ready user=%s", getattr(client.user, "id", "-"))
        await sync_registered_commands(
            tree=tree,
            client=client,
            admin_channels=admin_channels,
            discord_module=discord,
        )

    @client.event
    async def on_message(message) -> None:  # type: ignore[no-untyped-def]
        if message.author.bot:
            return
        if is_shutting_down():
            LOGGER.info("master.shutting_down dropping discord message")
            return

        event_ts = str(message.id)
        text = str(message.content or "")
        user_id = str(message.author.id)
        image_urls = _extract_image_urls(list(message.attachments))
        routed_attachments = _extract_routed_attachments(list(message.attachments))
        text_attachment_content = await _read_text_attachments(list(message.attachments))
        if text_attachment_content:
            text = f"{text}\n\n{text_attachment_content}".strip()
        is_mention = client.user is not None and client.user.mentioned_in(message)

        # Discord threads have a stable channel.id and a parent_id pointing to
        # the origin channel.  Map these to Slack equivalents:
        #   channel_id → parent channel (used for agent registry lookup)
        #   thread_ts  → thread channel id (stable thread identifier)
        # For regular channels a mention creates a new thread, so followup
        # messages inside it are routed without requiring another @mention.
        is_thread = isinstance(message.channel, discord.Thread)
        if is_thread:
            channel_id = str(message.channel.parent_id)
            thread_ts = str(message.channel.id)
        else:
            channel_id = str(message.channel.id)
            thread_ts = None  # set below after thread creation on mention

        try:
            admin_channel_id = thread_ts if is_thread else channel_id
            admin_message_command = (
                parse_admin_message_command(text) if admin_channel_id in admin_channels else None
            )
            if admin_message_command is not None:
                command_name, args_text = admin_message_command
                messages = _run_command(
                    command_name=command_name,
                    text=args_text,
                    channel_id=admin_channel_id,
                    user_id=user_id,
                )
                for reply in messages:
                    await _reply_message_chunks(message, reply)
                return

            if is_mention and not is_thread:
                # Mention in a regular channel — ack, spin up a thread, respond there.
                say_text = format_forward_ack(text=text, image_count=len(image_urls))
                ack_msg = await message.reply(say_text, mention_author=False)
                prompt_preview = DISCORD_COMMAND_PATTERN.sub("", text).strip()[:60] or "conversation"
                thread = await ack_msg.create_thread(
                    name=f"Agent: {prompt_preview}",
                    auto_archive_duration=1440,
                )
                thread_ts = str(thread.id)
                async with thread.typing():
                    with in_flight_dispatch():
                        response = await asyncio.to_thread(
                            router.route_mention_message,
                            platform="discord",
                            channel_id=channel_id,
                            text=text,
                            thread_ts=thread_ts,
                            event_ts=event_ts,
                            user_id=user_id,
                            image_urls=image_urls,
                            attachments=routed_attachments,
                        )
                await _reply_in_thread(thread, response)
                return

            if is_mention and is_thread:
                # Mention inside an existing thread — re-track and respond in thread.
                say_text = format_forward_ack(text=text, image_count=len(image_urls))
                await message.reply(say_text, mention_author=False)
                async with message.channel.typing():
                    with in_flight_dispatch():
                        response = await asyncio.to_thread(
                            router.route_mention_message,
                            platform="discord",
                            channel_id=channel_id,
                            text=text,
                            thread_ts=thread_ts,
                            event_ts=event_ts,
                            user_id=user_id,
                            image_urls=image_urls,
                            attachments=routed_attachments,
                        )
                await _reply_message_chunks(message, response)
                return

            # Followup in a thread without @mention.
            if not is_thread:
                return
            accepted = await asyncio.to_thread(
                router.accept_followup_message,
                platform="discord",
                channel_id=channel_id,
                text=text,
                thread_ts=thread_ts,
                event_ts=event_ts,
                user_id=user_id,
                image_urls=image_urls,
                attachments=routed_attachments,
            )
            if not accepted:
                return
            await _reply_message_chunks(message, format_forward_ack(text=text, image_count=len(image_urls)))
            async with message.channel.typing():
                with in_flight_dispatch():
                    routed = await asyncio.to_thread(
                        router.route_prompt,
                        platform="discord",
                        channel_id=channel_id,
                        text=text,
                        thread_ts=thread_ts,
                        user_id=user_id,
                        image_urls=image_urls,
                        attachments=routed_attachments,
                    )
            await _reply_message_chunks(message, routed)
        except RouteSkip as exc:
            LOGGER.info("discord route skipped for channel=%s reason=%s", channel_id, exc)
        except RouteError as exc:
            LOGGER.warning("discord route failed for channel=%s error=%s", channel_id, exc)
            await _reply_message_chunks(message, f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("master discord message handling failed")
            await _reply_message_chunks(message, f"Error: {exc}")

    @tree.command(name="master-agent-list", description="List all agents")
    async def cmd_list(interaction) -> None:  # type: ignore[no-untyped-def]
        await _execute_and_send(interaction, command_name="/master-agent-list", text="")

    @tree.command(name="master-agent-load", description="Load an agent mapping")
    async def cmd_load(
        interaction,
        name: str,
        repo_path: str,
        channel_id: str,
        branch: str = "main",
        adapter: str = "codex",
    ) -> None:  # type: ignore[no-untyped-def]
        text = f"{name} {repo_path} {channel_id} {branch} --adapter {adapter}".strip()
        await _execute_and_send(interaction, command_name="/master-agent-load", text=text)

    @tree.command(name="master-agent-start", description="Start an agent")
    async def cmd_start(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        await _execute_and_send(interaction, command_name="/master-agent-start", text=name)

    @tree.command(name="master-agent-stop", description="Stop an agent")
    async def cmd_stop(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        await _execute_and_send(interaction, command_name="/master-agent-stop", text=name)

    @tree.command(name="master-agent-status", description="Show agent status")
    async def cmd_status(interaction, name: str, full: bool = False) -> None:  # type: ignore[no-untyped-def]
        text = f"{name} --full" if full else name
        await _execute_and_send(interaction, command_name="/master-agent-status", text=text)

    @tree.command(name="master-agent-usage", description="Show agent usage")
    async def cmd_usage(interaction, name: str = "") -> None:  # type: ignore[no-untyped-def]
        await _execute_and_send(interaction, command_name="/master-agent-usage", text=name)

    @tree.command(name="master-agent-remove", description="Remove an agent")
    async def cmd_remove(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        await _execute_and_send(interaction, command_name="/master-agent-remove", text=name)

    @tree.command(name="master-agent-refresh-auth", description="Refresh agent auth in workspace")
    async def cmd_refresh_auth(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        await _execute_and_send(interaction, command_name="/master-agent-refresh-auth", text=name)

    @tree.command(name="master-agent-refresh-config", description="Push updated global Claude config to agent workspace")
    async def cmd_refresh_config(interaction, name: str) -> None:  # type: ignore[no-untyped-def]
        await _execute_and_send(interaction, command_name="/master-agent-refresh-config", text=name)

    @tree.command(name="master-agent-set-model", description="Set or clear an agent Claude model override")
    async def cmd_set_model(interaction, name: str, model: str | None = None) -> None:  # type: ignore[no-untyped-def]
        text = name if not model else f"{name} {model}"
        await _execute_and_send(interaction, command_name="/master-agent-set-model", text=text)

    client.run(bot_token)
