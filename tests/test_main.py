from __future__ import annotations

from src.bot.main import resolve_session_context


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
