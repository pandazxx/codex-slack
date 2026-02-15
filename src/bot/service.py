from __future__ import annotations

import re
from dataclasses import dataclass

from .runtime import RuntimeStatus, SessionRuntime

MENTION_PATTERN = re.compile(r"<@[^>]+>")


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

    def handle_prompt(self, channel_id: str, text: str) -> str:
        self._require_channel_access(channel_id)
        prompt = self.extract_prompt(text)
        if not prompt:
            raise ValueError("prompt is empty after removing mention")
        return self.runtime.submit_prompt(prompt=prompt, bridge=self.bridge)
