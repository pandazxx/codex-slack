from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import subprocess
from threading import Lock
from typing import Protocol

from .registry import AgentRegistry

LOGGER = logging.getLogger(__name__)
MENTION_PATTERN = re.compile(r"<@[^>]+>")


class RouteError(RuntimeError):
    pass


class AgentDispatcher(Protocol):
    def send_prompt(
        self,
        *,
        agent_name: str,
        container_name: str,
        prompt: str,
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
    ) -> str:
        ...


@dataclass(frozen=True)
class PodmanExecDispatcher:
    command_template: str = "codex exec -"
    timeout_seconds: int | None = None

    def send_prompt(
        self,
        *,
        agent_name: str,
        container_name: str,
        prompt: str,
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
    ) -> str:
        cmd = ["podman", "exec", "-i", container_name, "sh", "-lc", self.command_template]
        LOGGER.info(
            "router.dispatch_start agent=%s container=%s channel=%s thread_ts=%s user=%s prompt_chars=%d",
            agent_name,
            container_name,
            channel_id,
            thread_ts or "-",
            user_id or "-",
            len(prompt),
        )
        completed = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RouteError(completed.stderr.strip() or "dispatcher command failed")
        response = completed.stdout.strip()
        LOGGER.info(
            "router.dispatch_done agent=%s container=%s channel=%s thread_ts=%s user=%s response_chars=%d",
            agent_name,
            container_name,
            channel_id,
            thread_ts or "-",
            user_id or "-",
            len(response),
        )
        return response


@dataclass
class ChannelRouter:
    registry: AgentRegistry
    dispatcher: AgentDispatcher
    admin_channels: set[str]
    _tracked_threads: set[str] = field(default_factory=set, init=False, repr=False)
    _recent_mention_events: set[str] = field(default_factory=set, init=False, repr=False)
    _state_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def extract_prompt(self, text: str) -> str:
        return MENTION_PATTERN.sub("", text).strip()

    def _thread_key(self, channel_id: str, thread_ts: str) -> str:
        return f"{channel_id}:{thread_ts}"

    def _event_key(self, channel_id: str, ts: str) -> str:
        return f"{channel_id}:{ts}"

    def track_thread(self, channel_id: str, thread_ts: str | None) -> None:
        if not thread_ts:
            return
        with self._state_lock:
            self._tracked_threads.add(self._thread_key(channel_id, thread_ts))

    def is_tracked_thread(self, channel_id: str, thread_ts: str | None) -> bool:
        if not thread_ts:
            return False
        with self._state_lock:
            return self._thread_key(channel_id, thread_ts) in self._tracked_threads

    def mark_mention_event(self, channel_id: str, ts: str | None) -> None:
        if not ts:
            return
        with self._state_lock:
            self._recent_mention_events.add(self._event_key(channel_id, ts))
            if len(self._recent_mention_events) > 256:
                self._recent_mention_events = set(list(self._recent_mention_events)[-128:])

    def consume_marked_mention_event(self, channel_id: str, ts: str | None) -> bool:
        if not ts:
            return False
        key = self._event_key(channel_id, ts)
        with self._state_lock:
            if key not in self._recent_mention_events:
                return False
            self._recent_mention_events.remove(key)
            return True

    def route_prompt(
        self,
        *,
        channel_id: str,
        text: str,
        thread_ts: str | None,
        user_id: str | None,
    ) -> str:
        if channel_id in self.admin_channels:
            raise RouteError("admin channel is reserved for master commands")

        record = self.registry.find_by_channel(channel_id)
        if not record:
            raise RouteError(f"no mapped agent for channel {channel_id}")

        prompt = self.extract_prompt(text)
        if not prompt:
            raise RouteError("prompt is empty after removing mention")

        return self.dispatcher.send_prompt(
            agent_name=record.name,
            container_name=record.container_name,
            prompt=prompt,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
        )
