from __future__ import annotations

import logging
import shlex
import subprocess
from threading import Lock

LOGGER = logging.getLogger(__name__)


class CodexBridgeError(RuntimeError):
    pass


class LocalCodexBridge:
    """Subprocess bridge to local codex-cli.

    The command template may include `{session_id}`.
    Prompt text is provided over stdin.
    """

    def __init__(self, command_template: str, timeout_seconds: int | None = None, workspace_path: str | None = None) -> None:
        self._command_template = command_template
        self._timeout_seconds = timeout_seconds
        self._workspace_path = workspace_path
        self._process_lock = Lock()
        self._active_process: subprocess.Popen[str] | None = None
        self._cancel_requested = False

    def send_prompt(self, session_id: str, prompt: str) -> str:
        if "{session_id}" in self._command_template:
            command = self._command_template.format(session_id=session_id)
        else:
            command = self._command_template
        args = shlex.split(command)
        LOGGER.info(
            "codex.exec command=%r args=%r session_id=%s timeout=%s",
            command,
            args,
            session_id,
            self._timeout_seconds,
        )

        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self._workspace_path,
            )
            with self._process_lock:
                self._active_process = process
                self._cancel_requested = False

            stdout, stderr = process.communicate(input=prompt, timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise CodexBridgeError(f"codex-cli timed out after {self._timeout_seconds}s") from exc
        except OSError as exc:
            raise CodexBridgeError(f"failed to run codex-cli: {exc}") from exc
        finally:
            with self._process_lock:
                self._active_process = None

        stdout = stdout.strip()
        stderr = stderr.strip()

        if process.returncode != 0:
            if self._cancel_requested:
                raise CodexBridgeError("codex prompt cancelled")
            detail = stderr or stdout or f"exit code {process.returncode}"
            if "unexpected argument 'prompt' found" in detail and "session prompt" in command:
                detail = (
                    f"{detail}\nHint: your CODEX_COMMAND_TEMPLATE is incompatible with this codex version. "
                    "Try: codex exec - resume {session_id}"
                )
            raise CodexBridgeError(f"codex-cli failed: {detail}")

        if not stdout:
            return "(codex-cli returned an empty response)"
        return stdout

    def cancel_current_prompt(self) -> bool:
        with self._process_lock:
            if self._active_process is None:
                return False
            self._cancel_requested = True
            self._active_process.terminate()
            return True
