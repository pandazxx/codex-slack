from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import subprocess
from threading import Lock
import time
from typing import Protocol

from .registry import AgentRegistry

LOGGER = logging.getLogger(__name__)
MENTION_PATTERN = re.compile(r"<@[^>]+>")


class RouteError(RuntimeError):
    pass


class RouteSkip(RuntimeError):
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
    command_template: str = "codex exec --dangerously-bypass-approvals-and-sandbox resume --last -"
    timeout_seconds: int | None = None
    workdir: str = "/workspace/repo"
    codex_home: str = "/workspace/.codex"

    @staticmethod
    def _clip(value: str, limit: int = 240) -> str:
        text = value.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _session_id(*, channel_id: str, thread_ts: str | None) -> str:
        source = thread_ts or channel_id
        normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", source).strip("-")
        if not normalized:
            normalized = "session"
        return f"slack-{channel_id}-{normalized}"

    def _render_command(self, *, channel_id: str, thread_ts: str | None) -> tuple[str, str]:
        session_id = self._session_id(channel_id=channel_id, thread_ts=thread_ts)
        if "{session_id}" in self.command_template:
            return self.command_template.format(session_id=session_id), session_id

        stripped = self.command_template.rstrip()
        if "--last" in stripped:
            return stripped, session_id
        if stripped.endswith(" -"):
            return f"{stripped[:-2]} resume {session_id} -", session_id

        return self.command_template, session_id

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
        rendered_command, session_id = self._render_command(channel_id=channel_id, thread_ts=thread_ts)
        cmd = ["podman", "exec", "-i"]
        if self.codex_home:
            cmd.extend(["-e", f"CODEX_HOME={self.codex_home}"])
        if self.workdir:
            cmd.extend(["--workdir", self.workdir])
        cmd.extend([container_name, "sh", "-lc", rendered_command])
        LOGGER.info(
            "router.dispatch_start agent=%s container=%s channel=%s thread_ts=%s user=%s prompt_chars=%d workdir=%s codex_home=%s session_id=%s agent_command=%r",
            agent_name,
            container_name,
            channel_id,
            thread_ts or "-",
            user_id or "-",
            len(prompt),
            self.workdir or "-",
            self.codex_home or "-",
            session_id,
            rendered_command,
        )
        try:
            completed = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            LOGGER.warning(
                "router.dispatch_failed agent=%s container=%s channel=%s reason=timeout timeout_seconds=%s",
                agent_name,
                container_name,
                channel_id,
                self.timeout_seconds,
            )
            raise RouteError(f"dispatch timed out after {self.timeout_seconds}s") from exc
        except FileNotFoundError as exc:
            LOGGER.warning(
                "router.dispatch_failed agent=%s container=%s channel=%s reason=missing_binary error=%s",
                agent_name,
                container_name,
                channel_id,
                exc,
            )
            raise RouteError("podman CLI is not available in the master runtime") from exc

        if completed.returncode != 0:
            stderr_text = self._clip(completed.stderr)
            stdout_text = self._clip(completed.stdout)
            LOGGER.warning(
                "router.dispatch_failed agent=%s container=%s channel=%s exit_code=%s stderr=%r stdout=%r",
                agent_name,
                container_name,
                channel_id,
                completed.returncode,
                stderr_text,
                stdout_text,
            )
            details: list[str] = [f"exit={completed.returncode}"]
            if stderr_text:
                details.append(f"stderr={stderr_text}")
            if stdout_text:
                details.append(f"stdout={stdout_text}")
            raise RouteError(f"dispatch failed ({', '.join(details)})")
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
    _usage_by_agent: dict[str, dict[str, float]] = field(default_factory=dict, init=False, repr=False)
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
        image_urls: list[str] | None = None,
    ) -> str:
        if channel_id in self.admin_channels:
            raise RouteSkip("admin channel is reserved for master commands")

        record = self.registry.find_by_channel(channel_id)
        if not record:
            raise RouteSkip(f"no mapped agent for channel {channel_id}")

        prompt = self.extract_prompt(text)
        image_urls = image_urls or []
        if image_urls:
            image_lines = "\n".join(f"- {url}" for url in image_urls)
            prefix = f"{prompt}\n\n" if prompt else ""
            prompt = f"{prefix}Attached images:\n{image_lines}"

        if not prompt:
            raise RouteSkip("prompt is empty after removing mention")

        started_at = time.perf_counter()
        response = self.dispatcher.send_prompt(
            agent_name=record.name,
            container_name=record.container_name,
            prompt=prompt,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        self._record_usage(
            agent_name=record.name,
            prompt_chars=len(prompt),
            response_chars=len(response),
            image_count=len(image_urls),
            elapsed_ms=elapsed_ms,
        )
        return response

    def _record_usage(
        self,
        *,
        agent_name: str,
        prompt_chars: int,
        response_chars: int,
        image_count: int,
        elapsed_ms: float,
    ) -> None:
        with self._state_lock:
            entry = self._usage_by_agent.setdefault(
                agent_name,
                {
                    "prompt_count": 0.0,
                    "prompt_chars": 0.0,
                    "response_chars": 0.0,
                    "image_count": 0.0,
                    "total_latency_ms": 0.0,
                },
            )
            entry["prompt_count"] += 1
            entry["prompt_chars"] += prompt_chars
            entry["response_chars"] += response_chars
            entry["image_count"] += image_count
            entry["total_latency_ms"] += elapsed_ms

    def usage_summary(self, agent_name: str | None = None) -> list[dict[str, object]]:
        with self._state_lock:
            items = (
                {agent_name: self._usage_by_agent.get(agent_name, {})}
                if agent_name
                else dict(self._usage_by_agent)
            )

        result: list[dict[str, object]] = []
        for name, raw in sorted(items.items()):
            prompt_count = int(raw.get("prompt_count", 0.0))
            prompt_chars = int(raw.get("prompt_chars", 0.0))
            response_chars = int(raw.get("response_chars", 0.0))
            image_count = int(raw.get("image_count", 0.0))
            total_latency_ms = float(raw.get("total_latency_ms", 0.0))
            avg_latency_ms = (total_latency_ms / prompt_count) if prompt_count else 0.0
            result.append(
                {
                    "agent_name": name,
                    "prompt_count": prompt_count,
                    "prompt_chars": prompt_chars,
                    "response_chars": response_chars,
                    "image_count": image_count,
                    "total_latency_ms": round(total_latency_ms, 2),
                    "avg_latency_ms": round(avg_latency_ms, 2),
                }
            )
        return result
