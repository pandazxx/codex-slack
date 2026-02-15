from __future__ import annotations

import threading
import time

import pytest

from src.bot.runtime import SessionRuntime


class FakeBridge:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    def send_prompt(self, session_id: str, prompt: str) -> str:
        self.calls.append((session_id, prompt))
        if self.delay:
            time.sleep(self.delay)
        return f"reply:{prompt}"


def test_submit_prompt_requires_attachment() -> None:
    runtime = SessionRuntime()
    bridge = FakeBridge()

    with pytest.raises(RuntimeError):
        runtime.submit_prompt("hello", bridge)


def test_submit_prompt_serializes_calls() -> None:
    runtime = SessionRuntime(initial_session_id="sess_1")
    bridge = FakeBridge(delay=0.2)

    results: list[str] = []

    def worker(prompt: str) -> None:
        results.append(runtime.submit_prompt(prompt, bridge))

    first = threading.Thread(target=worker, args=("a",))
    second = threading.Thread(target=worker, args=("b",))

    first.start()
    time.sleep(0.05)
    status = runtime.status()
    assert status.busy is True
    assert status.queue_depth == 0

    second.start()
    time.sleep(0.05)
    status = runtime.status()
    assert status.queue_depth == 1

    first.join()
    second.join()

    assert len(results) == 2
    assert bridge.calls == [("sess_1", "a"), ("sess_1", "b")]


def test_attach_and_detach() -> None:
    runtime = SessionRuntime()

    runtime.attach("sess_2")
    assert runtime.status().session_id == "sess_2"

    runtime.detach()
    assert runtime.status().attached is False
