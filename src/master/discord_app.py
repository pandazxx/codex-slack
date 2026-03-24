from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .command_runtime import execute_master_command
from .dispatch_guard import in_flight_dispatch, is_shutting_down
from .dispatch_result import DispatchResult
from .file_converter import attachment_to_prompt_fragment
from .router import ChannelRouter, RouteError, RouteSkip
from .service import MasterService
from .slack_app import format_forward_ack

LOGGER = logging.getLogger(__name__)
DISCORD_COMMAND_PATTERN = re.compile(r"^<@!?\d+>\s*")
DISCORD_MESSAGE_LIMIT = 1900
DISCORD_FILE_THRESHOLD = 8000  # send as file attachment above this length

_MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_MD_TABLE_ROW = re.compile(r"^\|.+\|$")
_MD_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|$")

_TEXT_ATTACHMENT_SIZE_LIMIT = 20 * 1024 * 1024  # 20 MB

DISCORD_FILE_MAX_BYTES: int = int(os.environ.get("DISCORD_FILE_MAX_BYTES", "25000000"))
DISCORD_COMPRESSION_QUALITY: int = int(os.environ.get("DISCORD_COMPRESSION_QUALITY", "65"))


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


async def _read_all_attachments(attachments: list[Any]) -> str:
    """Download all non-image attachments, convert them, and return prompt text."""
    parts: list[str] = []
    for attachment in attachments:
        content_type = str(getattr(attachment, "content_type", "") or "").split(";")[0].strip()
        if content_type.startswith("image/"):
            continue
        filename = str(getattr(attachment, "filename", "") or "attachment")
        size = getattr(attachment, "size", 0) or 0
        if size > _TEXT_ATTACHMENT_SIZE_LIMIT:
            parts.append(
                f"[attachment {filename}: too large to include ({size:,} bytes)]"
            )
            continue
        try:
            raw = await attachment.read()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "discord.attachment_read_failed filename=%s error=%s", filename, exc
            )
            parts.append(f"[attachment: {filename} — read failed: {exc}]")
            continue
        fragment = attachment_to_prompt_fragment(
            filename=filename,
            mime_type=content_type,
            data=raw,
        )
        parts.append(fragment)
    return "\n\n".join(parts)


def _compress_for_discord(path: Path) -> Path | None:
    """Apply the tiered compression pipeline for Discord.

    Returns a path to the (possibly recompressed) file, or None if the file
    already fits within DISCORD_FILE_MAX_BYTES without any compression.
    Returns the original path unchanged if it already fits.
    Raises no exceptions — on hard failure the caller falls back to a notice.
    """
    size = path.stat().st_size
    if size <= DISCORD_FILE_MAX_BYTES:
        return path

    # Step 3: ZIP recompression + JPEG downsampling for embedded images
    try:
        import zipfile
        with zipfile.ZipFile(path, "r") as zin:
            names = zin.namelist()
        # Only try ZIP recompression if it actually is a ZIP
        with tempfile.NamedTemporaryFile(delete=False, suffix=path.suffix) as tmp:
            tmp_path = Path(tmp.name)
        with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
            for name in names:
                data = zin.read(name)
                lower = name.lower()
                if lower.endswith((".jpg", ".jpeg", ".png")) and len(data) > 10_000:
                    try:
                        from PIL import Image  # type: ignore[import-untyped]
                        img = Image.open(io.BytesIO(data))
                        buf = io.BytesIO()
                        img.convert("RGB").save(buf, format="JPEG", quality=DISCORD_COMPRESSION_QUALITY, optimize=True)
                        data = buf.getvalue()
                        name = re.sub(r"\.(png)$", ".jpg", name, flags=re.IGNORECASE)
                    except Exception:  # noqa: BLE001
                        pass
                zout.writestr(name, data)
        if tmp_path.stat().st_size <= DISCORD_FILE_MAX_BYTES:
            return tmp_path
        tmp_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass

    # Step 4: format conversion via LibreOffice + Ghostscript
    if shutil.which("libreoffice") is not None:
        try:
            with tempfile.TemporaryDirectory() as conv_dir:
                subprocess.run(
                    ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", conv_dir, str(path)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                pdf_files = list(Path(conv_dir).glob("*.pdf"))
                if pdf_files:
                    pdf_path = pdf_files[0]
                    if shutil.which("gs") is not None:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                            compressed_pdf = Path(tmp_pdf.name)
                        subprocess.run(
                            [
                                "gs", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
                                "-dPDFSETTINGS=/ebook",
                                f"-sOutputFile={compressed_pdf}", str(pdf_path),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=120,
                        )
                        if compressed_pdf.stat().st_size <= DISCORD_FILE_MAX_BYTES:
                            return compressed_pdf
                        compressed_pdf.unlink(missing_ok=True)
                    elif pdf_path.stat().st_size <= DISCORD_FILE_MAX_BYTES:
                        stable = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                        stable.close()
                        shutil.copy2(str(pdf_path), stable.name)
                        return Path(stable.name)
        except Exception:  # noqa: BLE001
            pass

    return None


async def _send_result_files_discord(message_or_thread: Any, result: DispatchResult) -> None:
    """Upload output files to Discord with the tiered compression pipeline."""
    try:
        import discord
    except ImportError:
        return

    guild = getattr(getattr(message_or_thread, "guild", None) or getattr(getattr(message_or_thread, "parent", None), "guild", None), "premium_tier", None)
    premium_tier = guild if isinstance(guild, int) else 0

    for path in result.file_paths:
        size = path.stat().st_size

        # Nitro fast-path: guild has boost tier 2+ (500 MB cap)
        if premium_tier >= 2 or size <= DISCORD_FILE_MAX_BYTES:
            try:
                with open(path, "rb") as fh:
                    await message_or_thread.send(file=discord.File(fh, filename=path.name))
                LOGGER.info("discord.file_uploaded filename=%s size=%d", path.name, size)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("discord.file_upload_failed filename=%s error=%s", path.name, exc)
            finally:
                path.unlink(missing_ok=True)
            continue

        # Try compression pipeline
        compressed = await asyncio.to_thread(_compress_for_discord, path)
        if compressed is not None and compressed.stat().st_size <= DISCORD_FILE_MAX_BYTES:
            try:
                with open(compressed, "rb") as fh:
                    await message_or_thread.send(file=discord.File(fh, filename=compressed.name))
                LOGGER.info(
                    "discord.file_uploaded_compressed original=%s original_size=%d compressed_size=%d",
                    path.name,
                    size,
                    compressed.stat().st_size,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("discord.file_upload_failed filename=%s error=%s", path.name, exc)
            finally:
                if compressed != path:
                    compressed.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
            continue

        # Hard failure
        size_mb = size / 1_000_000
        await message_or_thread.send(
            f"File is too large for Discord even after compression ({size_mb:.1f} MB)."
            f" It has been saved to the agent workspace at `{path}`."
            " Use Slack to retrieve it, or ask the agent to split it."
        )
        if compressed and compressed != path:
            compressed.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


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
        attachment_content = await _read_all_attachments(list(message.attachments))
        if attachment_content:
            text = f"{text}\n\n{attachment_content}".strip()
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
                        result = await asyncio.to_thread(
                            router.route_mention_message,
                            platform="discord",
                            channel_id=channel_id,
                            text=text,
                            thread_ts=thread_ts,
                            event_ts=event_ts,
                            user_id=user_id,
                            image_urls=image_urls,
                        )
                await _reply_in_thread(thread, result.text)
                await _send_result_files_discord(thread, result)
                return

            if is_mention and is_thread:
                # Mention inside an existing thread — re-track and respond in thread.
                say_text = format_forward_ack(text=text, image_count=len(image_urls))
                await message.reply(say_text, mention_author=False)
                async with message.channel.typing():
                    with in_flight_dispatch():
                        result = await asyncio.to_thread(
                            router.route_mention_message,
                            platform="discord",
                            channel_id=channel_id,
                            text=text,
                            thread_ts=thread_ts,
                            event_ts=event_ts,
                            user_id=user_id,
                            image_urls=image_urls,
                        )
                await _reply_message_chunks(message, result.text)
                await _send_result_files_discord(message.channel, result)
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
            )
            if not accepted:
                return
            await _reply_message_chunks(message, format_forward_ack(text=text, image_count=len(image_urls)))
            async with message.channel.typing():
                with in_flight_dispatch():
                    result = await asyncio.to_thread(
                        router.route_prompt,
                        platform="discord",
                        channel_id=channel_id,
                        text=text,
                        thread_ts=thread_ts,
                        user_id=user_id,
                        image_urls=image_urls,
                    )
            await _reply_message_chunks(message, result.text)
            await _send_result_files_discord(message.channel, result)
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

    client.run(bot_token)
