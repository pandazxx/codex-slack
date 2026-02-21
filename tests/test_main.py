from __future__ import annotations

from src.bot.main import format_startup_env_value, is_secret_env_key, mask_secret_value, resolve_session_context


def test_resolve_session_context_with_cli_session_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    session_id, explicit = resolve_session_context("sess_cli")
    assert session_id == "sess_cli"
    assert explicit is True


def test_resolve_session_context_with_env_session_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CODEX_SESSION_ID", "sess_env")
    session_id, explicit = resolve_session_context(None)
    assert session_id == "sess_env"
    assert explicit is True


def test_resolve_session_context_without_session_id_generates_auto(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    session_id, explicit = resolve_session_context(None)
    assert session_id.startswith("auto-")
    assert explicit is False


def test_is_secret_env_key_detects_token_and_key() -> None:
    assert is_secret_env_key("SLACK_BOT_TOKEN") is True
    assert is_secret_env_key("OPENAI_API_KEY") is True
    assert is_secret_env_key("CODEX_WORKSPACE_PATH") is False


def test_mask_secret_value_uses_three_char_edges_with_four_stars() -> None:
    assert mask_secret_value("abcdefghijklmnopqrstuvwxyz") == "abc****xyz"


def test_format_startup_env_value_masks_secrets() -> None:
    assert format_startup_env_value("SLACK_APP_TOKEN", "xapp-1234567890") == "xap****890"


def test_format_startup_env_value_keeps_non_secret() -> None:
    assert format_startup_env_value("CODEX_WORKSPACE_PATH", "/workspace") == "/workspace"
