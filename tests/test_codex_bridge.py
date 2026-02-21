from __future__ import annotations

import subprocess

import pytest

from src.bot.codex_bridge import CodexBridgeError, LocalCodexBridge


class FakePopen:
    def __init__(
        self,
        args: list[str],
        returncode: int = 0,
        stdout: str = "ok\n",
        stderr: str = "",
        timeout_error: bool = False,
    ) -> None:
        self.args = args
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout_error = timeout_error
        self.terminated = False
        self.killed = False

    def communicate(self, input: str | None = None, timeout: int | None = None) -> tuple[str, str]:
        if self._timeout_error and timeout is not None:
            self._timeout_error = False
            raise subprocess.TimeoutExpired(cmd=self.args, timeout=timeout)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    def terminate(self) -> None:
        self.terminated = True


def test_send_prompt_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakePopen(args=args)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    bridge = LocalCodexBridge("codex --session-id {session_id}")
    assert bridge.send_prompt("sess_1", "hello") == "ok"
    assert "sess_1" in captured["args"]


def test_send_prompt_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        return FakePopen(args=args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    bridge = LocalCodexBridge("codex --session-id {session_id}")

    with pytest.raises(CodexBridgeError, match="boom"):
        bridge.send_prompt("sess_1", "hello")


def test_send_prompt_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_process = FakePopen(args=["codex"], timeout_error=True)

    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    bridge = LocalCodexBridge("codex --session-id {session_id}", timeout_seconds=1)

    with pytest.raises(CodexBridgeError, match="timed out"):
        bridge.send_prompt("sess_1", "hello")
    assert fake_process.killed is True


def test_template_without_session_id_placeholder_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_args: list[str] = []

    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal seen_args
        seen_args = args
        return FakePopen(args=args)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    bridge = LocalCodexBridge("codex exec -")
    assert bridge.send_prompt("ignored_session", "hello") == "ok"
    assert seen_args == ["codex", "exec", "-"]


def test_send_prompt_includes_template_hint_for_old_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        return FakePopen(
            args=args,
            returncode=1,
            stdout="",
            stderr="error: unexpected argument 'prompt' found",
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    bridge = LocalCodexBridge("codex session prompt --session-id {session_id}")

    with pytest.raises(CodexBridgeError, match="Try: codex exec resume \\{session_id\\} -"):
        bridge.send_prompt("sess_1", "hello")


def test_cancel_current_prompt_without_active_process_returns_false() -> None:
    bridge = LocalCodexBridge("codex exec -")
    assert bridge.cancel_current_prompt() is False
