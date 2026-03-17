from __future__ import annotations

from src.master.discord_app import parse_admin_message_command


def test_parse_admin_message_command_accepts_plain_text_command() -> None:
    assert parse_admin_message_command("/master-agent-list") == ("/master-agent-list", "")


def test_parse_admin_message_command_accepts_mention_prefixed_command() -> None:
    assert parse_admin_message_command("<@123456789> /master-agent-start alpha") == (
        "/master-agent-start",
        "alpha",
    )


def test_parse_admin_message_command_rejects_non_command_text() -> None:
    assert parse_admin_message_command("hello there") is None
