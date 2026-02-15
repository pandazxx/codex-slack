from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.bot.codex_bridge import CodexBridgeError, LocalCodexBridge


def test_send_prompt_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        assert "sess_1" in args[0]
        assert kwargs["input"] == "hello"
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    bridge = LocalCodexBridge("codex --session-id {session_id}")
    assert bridge.send_prompt("sess_1", "hello") == "ok"


def test_send_prompt_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    bridge = LocalCodexBridge("codex --session-id {session_id}")

    with pytest.raises(CodexBridgeError, match="boom"):
        bridge.send_prompt("sess_1", "hello")


def test_send_prompt_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd="codex", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    bridge = LocalCodexBridge("codex --session-id {session_id}", timeout_seconds=1)

    with pytest.raises(CodexBridgeError, match="timed out"):
        bridge.send_prompt("sess_1", "hello")


def test_template_requires_session_id() -> None:
    with pytest.raises(ValueError, match="{session_id}"):
        LocalCodexBridge("codex prompt")
