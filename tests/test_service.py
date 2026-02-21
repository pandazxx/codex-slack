from __future__ import annotations

import logging

import pytest

from src.bot.runtime import SessionRuntime
from src.bot.service import AccessError, BotService


class FakeBridge:
    def send_prompt(self, session_id: str, prompt: str) -> str:
        return f"{session_id}:{prompt}"

    def cancel_current_prompt(self) -> bool:
        return True


def make_service() -> BotService:
    runtime = SessionRuntime(initial_session_id="sess_abc")
    return BotService(runtime=runtime, bridge=FakeBridge(), allowed_channels={"C1"})


def test_extract_prompt_removes_mentions() -> None:
    service = make_service()
    assert service.extract_prompt("<@U123> hello world") == "hello world"


def test_handle_prompt_rejects_non_allowlisted_channel() -> None:
    service = make_service()

    with pytest.raises(AccessError):
        service.handle_prompt("C2", "<@U123> hello")


def test_attach_detach_status_text() -> None:
    service = make_service()
    assert "attached=True" in service.status_text()

    service.detach()
    assert "attached=False" in service.status_text()
    assert "current_prompt=None" in service.status_text()

    message = service.attach("sess_new")
    assert "sess_new" in message


def test_cancel_current_conversation_returns_success_message() -> None:
    service = make_service()
    assert service.cancel_current_conversation() == "No running prompt to cancel"


def test_handle_prompt_uses_runtime() -> None:
    service = make_service()
    response = service.handle_prompt("C1", "<@U111> test")
    assert response == "sess_abc:test"


def test_handle_prompt_accepts_conversation_metadata() -> None:
    service = make_service()
    response = service.handle_prompt(
        "C1",
        "<@U111> test metadata",
        thread_ts="1730000000.1234",
        user_id="U123",
    )
    assert response == "sess_abc:test metadata"


def test_handle_prompt_logs_prompt_and_response_content(caplog: pytest.LogCaptureFixture) -> None:
    service = make_service()
    caplog.set_level(logging.INFO, logger="src.bot.service")

    response = service.handle_prompt(
        "C1",
        "<@U111> hello log",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert response == "sess_abc:hello log"
    text = caplog.text
    assert "conversation.prompt_content" in text
    assert "conversation.response_content" in text
    assert "'hello log'" in text
    assert "'sess_abc:hello log'" in text
