from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import Lock
import time
from typing import Callable, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .registry import AgentRegistry

LOGGER = logging.getLogger(__name__)
MENTION_PATTERN = re.compile(r"<@[^>]+>")


def _inject_claude_model(command: str, model: str) -> str:
    """Insert --model <model> right after 'claude' in the command string."""
    return re.sub(r"\bclaude\b", f"claude --model {model}", command, count=1)


class RouteError(RuntimeError):
    pass


class RouteSkip(RuntimeError):
    pass


class AgentDispatcher(Protocol):
    def send_prompt(
        self,
        *,
        agent_adapter: str = "codex",
        agent_name: str,
        container_name: str,
        prompt: str,
        platform: str = "slack",
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
        claude_model: str | None = None,
    ) -> str:
        ...


@dataclass(frozen=True)
class PodmanExecDispatcher:
    command_template: str = "codex exec --dangerously-bypass-approvals-and-sandbox resume --last -"
    timeout_seconds: int | None = None
    workdir: str = "/workspace/repo"
    codex_home: str = "/workspace/home/.codex"
    slack_bot_token: str | None = None
    agent_prepare_callback: Callable[[str], None] | None = None

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
            rendered = self.command_template.format(session_id=session_id)
            return self._ensure_claude_permission_bypass(rendered), session_id

        stripped = self.command_template.rstrip()
        if "--last" in stripped:
            return self._ensure_claude_permission_bypass(stripped), session_id
        if stripped.endswith(" -"):
            rendered = f"{stripped[:-2]} resume {session_id} -"
            return self._ensure_claude_permission_bypass(rendered), session_id

        return self._ensure_claude_permission_bypass(self.command_template), session_id

    @staticmethod
    def _ensure_claude_permission_bypass(command: str) -> str:
        stripped = command.rstrip()
        if not (stripped == "claude" or stripped.startswith("claude ")):
            return command
        if "--dangerously-skip-permissions" in stripped:
            return stripped
        return f"{stripped} --dangerously-skip-permissions"

    def _ensure_container_running(self, *, agent_name: str, container_name: str) -> None:
        try:
            inspected = subprocess.run(
                ["podman", "inspect", "--type", "container", container_name],
                text=True,
                capture_output=True,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RouteError(f"dispatch timed out after {self.timeout_seconds}s") from exc
        except FileNotFoundError as exc:
            LOGGER.warning(
                "router.dispatch_failed agent_container=%s reason=missing_binary error=%s",
                container_name,
                exc,
            )
            raise RouteError("podman CLI is not available in the master runtime") from exc

        if inspected.returncode != 0:
            stderr_text = self._clip(inspected.stderr)
            raise RouteError(f"agent container is not running and auto-start is not configured: {container_name}" + (f" ({stderr_text})" if stderr_text else ""))

        try:
            payload = json.loads(inspected.stdout)
        except json.JSONDecodeError as exc:
            raise RouteError(f"failed to inspect agent container {container_name}: invalid inspect output") from exc

        if not payload:
            raise RouteError(f"agent container is not available: {container_name}")

        state = payload[0].get("State", {})
        status = str(state.get("Status", "unknown"))
        running = bool(state.get("Running", False))
        if running:
            return

        raise RouteError(f"agent container is not running and auto-start is not configured: {container_name} (status={status})")

    def _prepare_agent_for_dispatch(self, *, agent_name: str) -> None:
        if self.agent_prepare_callback is None:
            return
        try:
            self.agent_prepare_callback(agent_name)
        except Exception as exc:  # noqa: BLE001
            raise RouteError(f"failed to prepare {agent_name}: {exc}") from exc

    def send_prompt(
        self,
        *,
        agent_adapter: str = "codex",
        agent_name: str,
        container_name: str,
        prompt: str,
        platform: str = "slack",
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
        claude_model: str | None = None,
    ) -> str:
        rendered_command, session_id = self._render_command(channel_id=channel_id, thread_ts=thread_ts)
        if claude_model:
            rendered_command = _inject_claude_model(rendered_command, claude_model)
        image_urls = image_urls or []
        self._prepare_agent_for_dispatch(agent_name=agent_name)
        self._ensure_container_running(agent_name=agent_name, container_name=container_name)
        staged_paths, passthrough_urls = self._stage_attachments(
            container_name=container_name,
            session_id=session_id,
            image_urls=image_urls,
        )
        effective_prompt = prompt
        if staged_paths or passthrough_urls:
            lines: list[str] = []
            for path in staged_paths:
                lines.append(f"- local file: {path}")
            for url in passthrough_urls:
                lines.append(f"- remote url: {url}")
            prefix = f"{effective_prompt}\n\n" if effective_prompt else ""
            effective_prompt = f"{prefix}Attached files:\n" + "\n".join(lines)
        LOGGER.info(
            "router.attachment_plan agent=%s container=%s platform=%s channel=%s thread_ts=%s input_urls=%d staged=%d passthrough=%d first_staged=%s first_passthrough=%s attachment_block=%s effective_prompt_chars=%d",
            agent_name,
            container_name,
            platform,
            channel_id,
            thread_ts or "-",
            len(image_urls),
            len(staged_paths),
            len(passthrough_urls),
            staged_paths[0] if staged_paths else "-",
            self._clip(passthrough_urls[0], 120) if passthrough_urls else "-",
            bool(staged_paths or passthrough_urls),
            len(effective_prompt),
        )
        cmd = ["podman", "exec", "-i"]
        if self.codex_home:
            cmd.extend(["-e", f"CODEX_HOME={self.codex_home}"])
        if self.workdir:
            cmd.extend(["--workdir", self.workdir])
        cmd.extend(["-e", f"AGENT_FRONTEND={platform}"])
        cmd.extend(["-e", f"AGENT_CHANNEL_ID={channel_id}"])
        cmd.extend(["-e", f"AGENT_ADAPTER={agent_adapter}"])
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
                input=effective_prompt,
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

    def _stage_attachments(
        self,
        *,
        container_name: str,
        session_id: str,
        image_urls: list[str],
    ) -> tuple[list[str], list[str]]:
        staged_paths: list[str] = []
        passthrough_urls: list[str] = []
        token = (self.slack_bot_token or "").strip()
        if not image_urls:
            return staged_paths, passthrough_urls

        for idx, url in enumerate(image_urls, start=1):
            tmp_path: str | None = None
            is_slack_file = "files.slack.com" in url
            if is_slack_file and not token:
                LOGGER.info(
                    "router.attachment_stage_passthrough container=%s reason=%s url=%s",
                    container_name,
                    "missing_slack_token",
                    self._clip(url, 120),
                )
                passthrough_urls.append(url)
                continue

            parsed = urlparse(url)
            ext = os.path.splitext(parsed.path)[1] or ".bin"
            rel_dir = f".attachments/{session_id}"
            rel_path = f"{rel_dir}/{time.time_ns()}-{idx}{ext}"
            target = f"{self.workdir.rstrip('/')}/{rel_path}" if self.workdir else rel_path
            try:
                self._run_quiet(["podman", "exec", container_name, "sh", "-lc", f"mkdir -p '{self.workdir.rstrip('/')}/{rel_dir}'"])
                tmp_path = self._download_attachment(url=url, token=token if is_slack_file else None, suffix=ext)
                self._run_quiet(["podman", "cp", tmp_path, f"{container_name}:{target}"])
                LOGGER.info(
                    "router.attachment_staged container=%s source_url=%s target=%s bytes=%d",
                    container_name,
                    self._clip(url, 120),
                    target,
                    os.path.getsize(tmp_path),
                )
                staged_paths.append(target)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "router.attachment_stage_failed container=%s url=%s error=%s",
                    container_name,
                    self._clip(url, 120),
                    exc,
                )
                passthrough_urls.append(url)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        return staged_paths, passthrough_urls

    def _download_attachment(self, *, url: str, token: str | None, suffix: str) -> str:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = Request(url, headers=headers)
        with urlopen(req, timeout=self.timeout_seconds or 30) as response:  # noqa: S310
            data = response.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            return tmp.name

    def _run_quiet(self, cmd: list[str]) -> None:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RouteError(f"command failed ({' '.join(cmd)}): {self._clip(completed.stderr)}")


@dataclass(frozen=True)
class MultiAgentDispatcher:
    dispatchers: dict[str, AgentDispatcher]
    default_adapter: str = "codex"

    def send_prompt(
        self,
        *,
        agent_adapter: str = "codex",
        agent_name: str,
        container_name: str,
        prompt: str,
        platform: str = "slack",
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
        claude_model: str | None = None,
    ) -> str:
        selected = (agent_adapter or self.default_adapter).strip().lower()
        dispatcher = self.dispatchers.get(selected) or self.dispatchers.get(self.default_adapter)
        if dispatcher is None:
            raise RouteError(f"unsupported agent adapter: {agent_adapter}")
        return dispatcher.send_prompt(
            agent_adapter=selected,
            agent_name=agent_name,
            container_name=container_name,
            prompt=prompt,
            platform=platform,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            image_urls=image_urls,
            claude_model=claude_model,
        )


@dataclass(frozen=True)
class ClaudeCodeDispatcher(PodmanExecDispatcher):
    command_template: str = "claude -p --output-format json"
    _channel_sessions: dict[str, str] = field(default_factory=dict, init=False, repr=False, compare=False)
    _session_lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    @staticmethod
    def _conversation_key(*, agent_name: str, platform: str, channel_id: str) -> str:
        return f"{agent_name}:{platform}:{channel_id}"

    @staticmethod
    def _session_name(*, agent_name: str, platform: str, channel_id: str) -> str:
        raw = f"claude-{agent_name}-{platform}-{channel_id}"
        normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
        return normalized or "claude-session"

    @staticmethod
    def _strip_session_flags(command: str) -> str:
        stripped = command.rstrip()
        patterns = [
            r"\s+--continue\b",
            r"\s+-n\b",
            r"\s+-r\s+\S+",
            r"\s+--resume\s+\S+",
            r"\s+--session-id\s+\S+",
        ]
        for pattern in patterns:
            stripped = re.sub(pattern, "", stripped)
        return stripped.strip()

    @staticmethod
    def _inject_session_flags(*, command: str, action: str, session_id: str) -> str:
        if action == "create":
            return re.sub(r"\bclaude\b", f"claude -n {session_id}", command, count=1)
        if action == "resume":
            return re.sub(r"\bclaude\b", f"claude -r {session_id}", command, count=1)
        raise ValueError(f"unsupported claude action: {action}")

    def _render_command(self, *, action: str, session_id: str) -> str:
        rendered = self._strip_session_flags(self.command_template)
        rendered = self._inject_session_flags(command=rendered, action=action, session_id=session_id)
        return self._ensure_claude_permission_bypass(rendered)

    @staticmethod
    def _should_retry_with_create(stderr_text: str) -> bool:
        lowered = stderr_text.lower()
        return (
            "no session" in lowered
            or "not found" in lowered
            or "no conversation" in lowered
            or "does not exist" in lowered
            or "unknown session" in lowered
        )

    def _execute_prompt(
        self,
        *,
        rendered_command: str,
        action: str,
        agent_adapter: str,
        agent_name: str,
        container_name: str,
        prompt: str,
        platform: str,
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
        effective_prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        cmd = ["podman", "exec", "-i"]
        if self.codex_home:
            cmd.extend(["-e", f"CODEX_HOME={self.codex_home}"])
        if self.workdir:
            cmd.extend(["--workdir", self.workdir])
        cmd.extend(["-e", f"AGENT_FRONTEND={platform}"])
        cmd.extend(["-e", f"AGENT_CHANNEL_ID={channel_id}"])
        cmd.extend(["-e", f"AGENT_ADAPTER={agent_adapter}"])
        cmd.extend([container_name, "sh", "-lc", rendered_command])
        LOGGER.info(
            "router.dispatch_start agent=%s container=%s platform=%s channel=%s thread_ts=%s user=%s prompt_chars=%d workdir=%s claude_action=%s agent_command=%r",
            agent_name,
            container_name,
            platform,
            channel_id,
            thread_ts or "-",
            user_id or "-",
            len(prompt),
            self.workdir or "-",
            action,
            rendered_command,
        )
        return subprocess.run(
            cmd,
            input=effective_prompt,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )

    @staticmethod
    def _parse_response(raw: str) -> str:
        """Extract text result from claude --output-format json output.

        Appends a compact usage footer (tokens + cost) when available.
        Falls back to returning raw output as-is if JSON parsing fails.
        """
        LOGGER.debug("router.parse_response_raw first_100=%r is_json_start=%s", raw[:100], raw.lstrip().startswith("{"))
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            LOGGER.warning("router.parse_response_not_json first_100=%r", raw[:100])
            return raw  # plain text mode or unparseable — return as-is

        result = str(data.get("result") or "").strip()
        usage = data.get("usage") or {}
        cost = data.get("total_cost_usd")

        # context_window is nested under modelUsage keyed by model name.
        context_window: int | None = None
        for model_data in (data.get("modelUsage") or {}).values():
            if isinstance(model_data, dict) and model_data.get("contextWindow"):
                context_window = int(model_data["contextWindow"])
                break

        parts: list[str] = []
        input_tokens = usage.get("input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_ctx = input_tokens + cache_creation + cache_read + output_tokens
        if input_tokens or output_tokens:
            parts.append(f"in={input_tokens:,} cache_hit={cache_read:,} out={output_tokens:,}")
        if total_ctx:
            ctx_str = f"ctx={total_ctx:,}"
            if context_window:
                ctx_str += f"/{context_window // 1000}k"
                pct = total_ctx / context_window * 100
                if pct <= 100:
                    ctx_str += f" ({pct:.1f}%)"
            parts.append(ctx_str)
        if cost is not None:
            parts.append(f"cost=${cost:.4f}")

        if parts:
            result = f"{result}\n\n`[{' | '.join(parts)}]`"
            LOGGER.info("router.parse_response_footer footer=%r", parts)
        else:
            LOGGER.warning("router.parse_response_no_footer usage=%r cost=%r", usage, cost)
        return result

    def send_prompt(
        self,
        *,
        agent_adapter: str = "claude-code",
        agent_name: str,
        container_name: str,
        prompt: str,
        platform: str = "slack",
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
        claude_model: str | None = None,
    ) -> str:
        LOGGER.info("router.claude_command_template template=%r", self.command_template)
        image_urls = image_urls or []
        self._prepare_agent_for_dispatch(agent_name=agent_name)
        self._ensure_container_running(agent_name=agent_name, container_name=container_name)
        staged_paths, passthrough_urls = self._stage_attachments(
            container_name=container_name,
            session_id=channel_id,
            image_urls=image_urls,
        )
        effective_prompt = prompt
        if staged_paths or passthrough_urls:
            lines: list[str] = []
            for path in staged_paths:
                lines.append(f"- local file: {path}")
            for url in passthrough_urls:
                lines.append(f"- remote url: {url}")
            prefix = f"{effective_prompt}\n\n" if effective_prompt else ""
            effective_prompt = f"{prefix}Attached files:\n" + "\n".join(lines)

        conversation_key = self._conversation_key(
            agent_name=agent_name,
            platform=platform,
            channel_id=channel_id,
        )
        with self._session_lock:
            known_session_id = self._channel_sessions.get(conversation_key)
        initial_action = "resume" if known_session_id else "create"
        initial_session_id = known_session_id or self._session_name(
            agent_name=agent_name,
            platform=platform,
            channel_id=channel_id,
        )
        retry_action: str | None = None
        effective_session_id = initial_session_id
        rendered_command = self._render_command(action=initial_action, session_id=effective_session_id)
        if claude_model:
            rendered_command = _inject_claude_model(rendered_command, claude_model)
        try:
            completed = self._execute_prompt(
                rendered_command=rendered_command,
                action=initial_action,
                agent_adapter=agent_adapter,
                agent_name=agent_name,
                container_name=container_name,
                prompt=prompt,
                platform=platform,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                effective_prompt=effective_prompt,
            )
        except subprocess.TimeoutExpired as exc:
            LOGGER.warning(
                "router.dispatch_failed agent=%s container=%s platform=%s channel=%s reason=timeout timeout_seconds=%s",
                agent_name,
                container_name,
                platform,
                channel_id,
                self.timeout_seconds,
            )
            raise RouteError(f"dispatch timed out after {self.timeout_seconds}s") from exc
        except FileNotFoundError as exc:
            LOGGER.warning(
                "router.dispatch_failed agent=%s container=%s platform=%s channel=%s reason=missing_binary error=%s",
                agent_name,
                container_name,
                platform,
                channel_id,
                exc,
            )
            raise RouteError("podman CLI is not available in the master runtime") from exc

        if completed.returncode != 0:
            stderr_text = self._clip(completed.stderr)
            if self._should_retry_with_create(stderr_text):
                retry_action = "create"
                with self._session_lock:
                    self._channel_sessions.pop(conversation_key, None)
                effective_session_id = self._session_name(
                    agent_name=agent_name,
                    platform=platform,
                    channel_id=channel_id,
                )
                LOGGER.info(
                    "router.dispatch_retry agent=%s container=%s platform=%s channel=%s thread_ts=%s user=%s claude_action=%s claude_retry_action=%s session_id=%s retry_reason=%r",
                    agent_name,
                    container_name,
                    platform,
                    channel_id,
                    thread_ts or "-",
                    user_id or "-",
                    initial_action,
                    retry_action,
                    effective_session_id,
                    stderr_text,
                )
                rendered_command = self._render_command(action=retry_action, session_id=effective_session_id)
                if claude_model:
                    rendered_command = _inject_claude_model(rendered_command, claude_model)
                try:
                    completed = self._execute_prompt(
                        rendered_command=rendered_command,
                        action=retry_action,
                        agent_adapter=agent_adapter,
                        agent_name=agent_name,
                        container_name=container_name,
                        prompt=prompt,
                        platform=platform,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                        user_id=user_id,
                        effective_prompt=effective_prompt,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RouteError(f"dispatch timed out after {self.timeout_seconds}s") from exc
                except FileNotFoundError as exc:
                    raise RouteError("podman CLI is not available in the master runtime") from exc

        if completed.returncode != 0:
            stderr_text = self._clip(completed.stderr)
            stdout_text = self._clip(completed.stdout)
            LOGGER.warning(
                "router.dispatch_failed agent=%s container=%s platform=%s channel=%s claude_action=%s exit_code=%s stderr=%r stdout=%r",
                agent_name,
                container_name,
                platform,
                channel_id,
                retry_action or initial_action,
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

        response = self._parse_response(completed.stdout.strip())
        with self._session_lock:
            self._channel_sessions[conversation_key] = effective_session_id
        LOGGER.info(
            "router.dispatch_done agent=%s container=%s platform=%s channel=%s thread_ts=%s user=%s claude_action=%s session_id=%s response_chars=%d",
            agent_name,
            container_name,
            platform,
            channel_id,
            thread_ts or "-",
            user_id or "-",
            retry_action or initial_action,
            effective_session_id,
            len(response),
        )
        return response


@dataclass
class ChannelRouter:
    registry: AgentRegistry
    dispatcher: AgentDispatcher
    admin_channels: set[str]
    tracked_threads_path: str | None = None
    _tracked_threads: set[str] = field(default_factory=set, init=False, repr=False)
    _recent_mention_events: set[str] = field(default_factory=set, init=False, repr=False)
    _usage_by_agent: dict[str, dict[str, float]] = field(default_factory=dict, init=False, repr=False)
    _state_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tracked_threads = self._load_tracked_threads()

    def extract_prompt(self, text: str) -> str:
        return MENTION_PATTERN.sub("", text).strip()

    def _thread_key(self, platform: str, channel_id: str, thread_ts: str) -> str:
        return f"{platform}:{channel_id}:{thread_ts}"

    def _legacy_thread_key(self, channel_id: str, thread_ts: str) -> str:
        return f"{channel_id}:{thread_ts}"

    def _event_key(self, platform: str, channel_id: str, ts: str) -> str:
        return f"{platform}:{channel_id}:{ts}"

    def _legacy_event_key(self, channel_id: str, ts: str) -> str:
        return f"{channel_id}:{ts}"

    def track_thread(self, channel_id: str, thread_ts: str | None, platform: str = "slack") -> None:
        if not thread_ts:
            return
        with self._state_lock:
            self._tracked_threads.add(self._thread_key(platform, channel_id, thread_ts))
            self._persist_tracked_threads_unlocked()

    def is_tracked_thread(self, channel_id: str, thread_ts: str | None, platform: str = "slack") -> bool:
        if not thread_ts:
            return False
        with self._state_lock:
            return (
                self._thread_key(platform, channel_id, thread_ts) in self._tracked_threads
                or self._legacy_thread_key(channel_id, thread_ts) in self._tracked_threads
            )

    def mark_mention_event(self, channel_id: str, ts: str | None, platform: str = "slack") -> None:
        if not ts:
            return
        with self._state_lock:
            self._recent_mention_events.add(self._event_key(platform, channel_id, ts))
            if len(self._recent_mention_events) > 256:
                self._recent_mention_events = set(list(self._recent_mention_events)[-128:])

    def consume_marked_mention_event(self, channel_id: str, ts: str | None, platform: str = "slack") -> bool:
        if not ts:
            return False
        key = self._event_key(platform, channel_id, ts)
        legacy_key = self._legacy_event_key(channel_id, ts)
        with self._state_lock:
            if key in self._recent_mention_events:
                self._recent_mention_events.remove(key)
                return True
            if legacy_key in self._recent_mention_events:
                self._recent_mention_events.remove(legacy_key)
                return True
            return False

    def route_prompt(
        self,
        *,
        platform: str = "slack",
        channel_id: str,
        text: str,
        thread_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
    ) -> str:
        if channel_id in self.admin_channels:
            raise RouteSkip("admin channel is reserved for master commands")

        record = self.registry.find_by_channel(channel_id, platform=platform)
        if not record:
            raise RouteSkip(f"no mapped agent for channel {platform}:{channel_id}")

        prompt = self.extract_prompt(text)
        image_urls = image_urls or []
        if not prompt and not image_urls:
            raise RouteSkip("prompt is empty after removing mention")

        started_at = time.perf_counter()
        response = self.dispatcher.send_prompt(
            agent_adapter=record.agent_adapter,
            agent_name=record.name,
            container_name=record.container_name,
            prompt=prompt,
            platform=platform,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            image_urls=image_urls,
            claude_model=record.claude_model,
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

    def route_mention_message(
        self,
        *,
        platform: str = "slack",
        channel_id: str,
        text: str,
        thread_ts: str | None,
        event_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
    ) -> str:
        self.track_thread(channel_id=channel_id, thread_ts=thread_ts, platform=platform)
        self.mark_mention_event(channel_id=channel_id, ts=event_ts, platform=platform)
        return self.route_prompt(
            platform=platform,
            channel_id=channel_id,
            text=text,
            thread_ts=thread_ts,
            user_id=user_id,
            image_urls=image_urls,
        )

    def route_followup_message(
        self,
        *,
        platform: str = "slack",
        channel_id: str,
        text: str,
        thread_ts: str | None,
        event_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
    ) -> str | None:
        if not self.accept_followup_message(
            platform=platform,
            channel_id=channel_id,
            text=text,
            thread_ts=thread_ts,
            event_ts=event_ts,
            user_id=user_id,
            image_urls=image_urls,
        ):
            return None
        return self.route_prompt(
            platform=platform,
            channel_id=channel_id,
            text=text,
            thread_ts=thread_ts,
            user_id=user_id,
            image_urls=image_urls,
        )

    def accept_followup_message(
        self,
        *,
        platform: str = "slack",
        channel_id: str,
        text: str,
        thread_ts: str | None,
        event_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
    ) -> bool:
        image_urls = image_urls or []
        if not thread_ts or not user_id or (not text and not image_urls):
            return False
        if self.consume_marked_mention_event(channel_id=channel_id, ts=event_ts, platform=platform):
            return False
        if not self.is_tracked_thread(channel_id=channel_id, thread_ts=thread_ts, platform=platform):
            return False
        return True

    def _load_tracked_threads(self) -> set[str]:
        path = (self.tracked_threads_path or "").strip()
        if not path:
            return set()
        state_path = Path(path)
        if not state_path.exists():
            return set()
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("router.thread_state_load_failed path=%s error=%s", path, exc)
            return set()
        entries = raw.get("tracked_threads")
        if not isinstance(entries, list):
            return set()
        return {str(item) for item in entries if isinstance(item, str)}

    def _persist_tracked_threads_unlocked(self) -> None:
        path = (self.tracked_threads_path or "").strip()
        if not path:
            return
        state_path = Path(path)
        payload = {"tracked_threads": sorted(self._tracked_threads)}
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("router.thread_state_persist_failed path=%s error=%s", path, exc)

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
