from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol


class PromptBridge(Protocol):
    def send_prompt(self, session_id: str, prompt: str) -> str:
        ...

    def cancel_current_prompt(self) -> bool:
        ...


@dataclass(frozen=True)
class RuntimeStatus:
    attached: bool
    session_id: str | None
    queue_depth: int
    busy: bool
    current_prompt: str | None
    last_error: str | None


class SessionRuntime:
    """Manages a single attached Codex session and serial prompt execution."""

    def __init__(self, initial_session_id: str | None = None) -> None:
        self._session_id = initial_session_id
        self._busy = False
        self._pending = 0
        self._current_prompt: str | None = None
        self._last_error: str | None = None
        self._state_lock = Lock()
        self._process_lock = Lock()

    def attach(self, session_id: str) -> None:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session_id cannot be empty")
        with self._state_lock:
            self._session_id = session_id
            self._last_error = None

    def detach(self) -> None:
        with self._state_lock:
            self._session_id = None

    def status(self) -> RuntimeStatus:
        with self._state_lock:
            return RuntimeStatus(
                attached=self._session_id is not None,
                session_id=self._session_id,
                queue_depth=self._pending,
                busy=self._busy,
                current_prompt=self._current_prompt,
                last_error=self._last_error,
            )

    def _require_session_id(self) -> str:
        with self._state_lock:
            if not self._session_id:
                raise RuntimeError("No session attached")
            return self._session_id

    def submit_prompt(self, prompt: str, bridge: PromptBridge) -> str:
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")

        with self._state_lock:
            self._pending += 1

        with self._process_lock:
            with self._state_lock:
                self._pending -= 1
                self._busy = True
                self._current_prompt = prompt

            try:
                session_id = self._require_session_id()
                response = bridge.send_prompt(session_id=session_id, prompt=prompt)
                with self._state_lock:
                    self._last_error = None
                return response
            except Exception as exc:  # noqa: BLE001
                with self._state_lock:
                    self._last_error = str(exc)
                raise
            finally:
                with self._state_lock:
                    self._busy = False
                    self._current_prompt = None

    def cancel_current_prompt(self, bridge: PromptBridge) -> bool:
        with self._state_lock:
            if not self._busy:
                return False
        return bridge.cancel_current_prompt()
