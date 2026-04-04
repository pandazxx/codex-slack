from __future__ import annotations

import asyncio

from src.master.discord_app import _extract_attachment_urls
from src.master.discord_app import build_discord_reply_plan
from src.master.discord_app import parse_admin_message_command
from src.master.discord_app import split_discord_message
from src.master.discord_app import sync_registered_commands
from src.master.response_split import SPLIT_HINT_LINE, split_on_hint_lines


def test_parse_admin_message_command_accepts_plain_text_command() -> None:
    assert parse_admin_message_command("/master-agent-list") == ("/master-agent-list", "")


def test_parse_admin_message_command_accepts_mention_prefixed_command() -> None:
    assert parse_admin_message_command("<@123456789> /master-agent-start alpha") == (
        "/master-agent-start",
        "alpha",
    )


def test_parse_admin_message_command_rejects_non_command_text() -> None:
    assert parse_admin_message_command("hello there") is None


def test_split_discord_message_returns_single_chunk_when_short() -> None:
    assert split_discord_message("hello") == ["hello"]


def test_split_discord_message_chunks_long_payload() -> None:
    text = ("a" * 1990) + "\n" + ("b" * 50)
    chunks = split_discord_message(text, limit=2000)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 1990
    assert chunks[1] == "b" * 50


def test_split_on_hint_lines_preserves_visible_marker_with_next_section() -> None:
    parts = split_on_hint_lines(f"alpha\n\n{SPLIT_HINT_LINE}\n\nbeta")
    assert parts == ["alpha", f"{SPLIT_HINT_LINE}\n\nbeta"]


def test_split_on_hint_lines_requires_exact_match() -> None:
    parts = split_on_hint_lines("alpha\n 🔹🔹🔹 \nbeta")
    assert parts == ["alpha\n 🔹🔹🔹 \nbeta"]


def test_build_discord_reply_plan_uses_hint_sections_when_within_limit() -> None:
    plan = build_discord_reply_plan(f"alpha\n\n{SPLIT_HINT_LINE}\n\nbeta")
    assert plan.send_as_file is False
    assert plan.messages == ["alpha", f"{SPLIT_HINT_LINE}\n\nbeta"]


def test_build_discord_reply_plan_falls_back_to_file_when_hinted_section_too_long() -> None:
    oversized = "a" * 1901
    plan = build_discord_reply_plan(f"alpha\n\n{SPLIT_HINT_LINE}\n\n{oversized}")
    assert plan.send_as_file is True
    assert plan.file_text == f"alpha\n\n{SPLIT_HINT_LINE}\n\n{oversized}"


def test_build_discord_reply_plan_uses_file_for_very_large_unhinted_response() -> None:
    plan = build_discord_reply_plan("x" * 8100)
    assert plan.send_as_file is True
    assert plan.file_text == "x" * 8100


def test_extract_attachment_urls_keeps_non_image_files() -> None:
    class Attachment:
        def __init__(self, url: str, content_type: str) -> None:
            self.url = url
            self.content_type = content_type

    urls = _extract_attachment_urls(
        [
            Attachment("https://cdn.discordapp.com/a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            Attachment("https://cdn.discordapp.com/b.png", "image/png"),
        ]
    )
    assert urls == ["https://cdn.discordapp.com/a.docx", "https://cdn.discordapp.com/b.png"]


def test_sync_registered_commands_copies_global_commands_to_admin_guild() -> None:
    class FakeGuild:
        def __init__(self, guild_id: int) -> None:
            self.id = guild_id

    class FakeChannel:
        def __init__(self, guild_id: int) -> None:
            self.guild = FakeGuild(guild_id)

    class FakeClient:
        def get_channel(self, channel_id: int):  # type: ignore[no-untyped-def]
            return FakeChannel(999) if channel_id == 123 else None

        async def fetch_channel(self, channel_id: int):  # type: ignore[no-untyped-def]
            return FakeChannel(777)

    class FakeTree:
        def __init__(self) -> None:
            self.copied: list[int] = []
            self.synced: list[int | None] = []

        def copy_global_to(self, guild) -> None:  # type: ignore[no-untyped-def]
            self.copied.append(guild.id)

        async def sync(self, guild=None):  # type: ignore[no-untyped-def]
            self.synced.append(None if guild is None else guild.id)

    class FakeDiscord:
        class Object:
            def __init__(self, id: int) -> None:
                self.id = id

    tree = FakeTree()
    client = FakeClient()

    asyncio.run(
        sync_registered_commands(
            tree=tree,
            client=client,
            admin_channels={"123"},
            discord_module=FakeDiscord,
        )
    )

    assert tree.copied == [999]
    assert tree.synced == [999, None]
