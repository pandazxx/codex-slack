from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from .runtime import RuntimeStatus, SessionRuntime

MENTION_PATTERN = re.compile(r"<@[^>]+>")
LOGGER = logging.getLogger(__name__)


class AccessError(RuntimeError):
    pass


@dataclass
class BotService:
    runtime: SessionRuntime
    bridge: object
    allowed_channels: set[str]

    def is_allowed_channel(self, channel_id: str) -> bool:
        return channel_id in self.allowed_channels

    def _require_channel_access(self, channel_id: str) -> None:
        if not self.is_allowed_channel(channel_id):
            raise AccessError(f"channel {channel_id} is not allowlisted")

    def extract_prompt(self, text: str) -> str:
        prompt = MENTION_PATTERN.sub("", text).strip()
        return prompt

    def status(self) -> RuntimeStatus:
        return self.runtime.status()

    def status_text(self) -> str:
        status = self.status()
        return (
            f"attached={status.attached} "
            f"session_id={status.session_id or '-'} "
            f"busy={status.busy} "
            f"queue_depth={status.queue_depth} "
            f"last_error={status.last_error or '-'}"
        )

    def attach(self, session_id: str) -> str:
        self.runtime.attach(session_id)
        return f"Attached to session `{session_id}`"

    def detach(self) -> str:
        self.runtime.detach()
        return "Detached from session"

    def handle_prompt(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
        user_id: str | None = None,
    ) -> str:
        started_at = time.perf_counter()
        self._require_channel_access(channel_id)
        prompt = self.extract_prompt(text)
        if not prompt:
            raise ValueError("prompt is empty after removing mention")

        status_before = self.runtime.status()
        LOGGER.info(
            (
                "conversation.received channel_id=%s thread_ts=%s user_id=%s "
                "session_id=%s busy=%s queue_depth=%s prompt_chars=%d"
            ),
            channel_id,
            thread_ts or "-",
            user_id or "-",
            status_before.session_id or "-",
            status_before.busy,
            status_before.queue_depth,
            len(prompt),
        )

        try:
            response = self.runtime.submit_prompt(prompt=prompt, bridge=self.bridge)
            duration_ms = (time.perf_counter() - started_at) * 1000
            LOGGER.info(
                "conversation.completed channel_id=%s thread_ts=%s user_id=%s session_id=%s response_chars=%d duration_ms=%.2f",
                channel_id,
                thread_ts or "-",
                user_id or "-",
                self.runtime.status().session_id or "-",
                len(response),
                duration_ms,
            )
            return response
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            LOGGER.exception(
                "conversation.failed channel_id=%s thread_ts=%s user_id=%s session_id=%s duration_ms=%.2f",
                channel_id,
                thread_ts or "-",
                user_id or "-",
                self.runtime.status().session_id or "-",
                duration_ms,
            )
            raise
