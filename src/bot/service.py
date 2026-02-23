from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from threading import Lock

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
    _tracked_threads: set[str] = field(default_factory=set, init=False, repr=False)
    _recent_mention_events: set[str] = field(default_factory=set, init=False, repr=False)
    _event_state_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def is_allowed_channel(self, channel_id: str) -> bool:
        return channel_id in self.allowed_channels

    def _require_channel_access(self, channel_id: str) -> None:
        if not self.is_allowed_channel(channel_id):
            raise AccessError(f"channel {channel_id} is not allowlisted")

    def _thread_key(self, channel_id: str, thread_ts: str) -> str:
        return f"{channel_id}:{thread_ts}"

    def _event_key(self, channel_id: str, ts: str) -> str:
        return f"{channel_id}:{ts}"

    def extract_prompt(self, text: str) -> str:
        prompt = MENTION_PATTERN.sub("", text).strip()
        return prompt

    def track_thread(self, channel_id: str, thread_ts: str | None) -> None:
        if not thread_ts:
            return
        with self._event_state_lock:
            self._tracked_threads.add(self._thread_key(channel_id, thread_ts))

    def is_tracked_thread(self, channel_id: str, thread_ts: str | None) -> bool:
        if not thread_ts:
            return False
        with self._event_state_lock:
            return self._thread_key(channel_id, thread_ts) in self._tracked_threads

    def mark_mention_event(self, channel_id: str, ts: str | None) -> None:
        if not ts:
            return
        with self._event_state_lock:
            self._recent_mention_events.add(self._event_key(channel_id, ts))
            if len(self._recent_mention_events) > 256:
                # Bound memory use; exact LRU behavior is unnecessary for this dedupe cache.
                self._recent_mention_events = set(list(self._recent_mention_events)[-128:])

    def consume_marked_mention_event(self, channel_id: str, ts: str | None) -> bool:
        if not ts:
            return False
        key = self._event_key(channel_id, ts)
        with self._event_state_lock:
            if key not in self._recent_mention_events:
                return False
            self._recent_mention_events.remove(key)
            return True

    def status(self) -> RuntimeStatus:
        return self.runtime.status()

    def status_text(self) -> str:
        status = self.status()
        return (
            f"attached={status.attached} "
            f"session_id={status.session_id or '-'} "
            f"busy={status.busy} "
            f"queue_depth={status.queue_depth} "
            f"current_prompt={status.current_prompt!r} "
            f"last_error={status.last_error or '-'}"
        )

    def attach(self, session_id: str) -> str:
        self.runtime.attach(session_id)
        return f"Attached to session `{session_id}`"

    def detach(self) -> str:
        self.runtime.detach()
        return "Detached from session"

    def cancel_current_conversation(self) -> str:
        cancelled = self.runtime.cancel_current_prompt(self.bridge)
        if not cancelled:
            return "No running prompt to cancel"
        return "Cancellation signal sent to current prompt"

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
        conversation_key = f"{channel_id}:{thread_ts or '-'}"

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
        LOGGER.info(
            "conversation.prompt_content conversation=%s user_id=%s prompt=%r",
            conversation_key,
            user_id or "-",
            prompt,
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
            LOGGER.info(
                "conversation.response_content conversation=%s user_id=%s response=%r",
                conversation_key,
                user_id or "-",
                response,
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
            LOGGER.info(
                "conversation.failed_prompt_content conversation=%s user_id=%s prompt=%r",
                conversation_key,
                user_id or "-",
                prompt,
            )
            raise
