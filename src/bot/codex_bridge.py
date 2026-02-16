from __future__ import annotations

import shlex
import subprocess


class CodexBridgeError(RuntimeError):
    pass


class LocalCodexBridge:
    """Subprocess bridge to local codex-cli.

    The command template must include `{session_id}`.
    Prompt text is provided over stdin.
    """

    def __init__(self, command_template: str, timeout_seconds: int = 120, workspace_path: str | None = None) -> None:
        if "{session_id}" not in command_template:
            raise ValueError("command_template must include {session_id}")
        self._command_template = command_template
        self._timeout_seconds = timeout_seconds
        self._workspace_path = workspace_path

    def send_prompt(self, session_id: str, prompt: str) -> str:
        command = self._command_template.format(session_id=session_id)
        args = shlex.split(command)

        try:
            completed = subprocess.run(
                args,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
                cwd=self._workspace_path,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexBridgeError(f"codex-cli timed out after {self._timeout_seconds}s") from exc
        except OSError as exc:
            raise CodexBridgeError(f"failed to run codex-cli: {exc}") from exc

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()

        if completed.returncode != 0:
            detail = stderr or stdout or f"exit code {completed.returncode}"
            if "unexpected argument 'prompt' found" in detail and "session prompt" in command:
                detail = (
                    f"{detail}\nHint: your CODEX_COMMAND_TEMPLATE is incompatible with this codex version. "
                    "Try: codex exec resume {session_id} -"
                )
            raise CodexBridgeError(f"codex-cli failed: {detail}")

        if not stdout:
            return "(codex-cli returned an empty response)"
        return stdout
