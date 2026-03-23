from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class RuntimeErrorAdapter(RuntimeError):
    pass


class RuntimeAdapter(Protocol):
    def build_image(self, *, name: str, repo_path: str, context_rel: str, dockerfile_rel: str) -> str:
        ...

    def create_or_update_agent(
        self,
        *,
        container_name: str,
        image: str,
        repo_volume: str,
        env: dict[str, str] | None = None,
        mounts: list[str] | None = None,
    ) -> None:
        ...

    def start_agent(self, name: str) -> None:
        ...

    def stop_agent(self, name: str) -> None:
        ...

    def remove_agent(self, name: str) -> None:
        ...

    def refresh_agent_auth(self, *, volume_name: str, host_auth_path: str) -> None:
        ...

    def refresh_agent_config(self, *, volume_name: str, host_config_dir: str) -> None:
        ...

    def inspect_agent(self, name: str) -> dict[str, Any] | None:
        ...

    def tail_logs(self, name: str, lines: int) -> str:
        ...


@dataclass
class PodmanRuntimeAdapter:
    dry_run: bool = False

    def _run(self, cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        if self.dry_run:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        executable = cmd[0]
        if shutil.which(executable) is None:
            raise RuntimeErrorAdapter(
                f"{executable} CLI is not installed in the master runtime; "
                "rebuild the image with the client binary available"
            )

        completed = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            text=True,
            capture_output=True,
        )
        if check and completed.returncode != 0:
            raise RuntimeErrorAdapter(f"Command failed ({' '.join(cmd)}): {completed.stderr.strip()}")
        return completed

    def _container_exists(self, container_name: str) -> bool:
        completed = self._run(["podman", "container", "exists", container_name], check=False)
        return completed.returncode == 0

    def build_image(self, *, name: str, repo_path: str, context_rel: str, dockerfile_rel: str) -> str:
        image_tag = f"codex-agent-{name}:latest"
        context_dir = Path(repo_path) / context_rel
        dockerfile = Path(repo_path) / dockerfile_rel
        self._run(
            [
                "podman",
                "build",
                "-t",
                image_tag,
                "-f",
                str(dockerfile),
                str(context_dir),
            ]
        )
        return image_tag

    def create_or_update_agent(
        self,
        *,
        container_name: str,
        image: str,
        repo_volume: str,
        env: dict[str, str] | None = None,
        mounts: list[str] | None = None,
    ) -> None:
        if self._container_exists(container_name):
            self._run(["podman", "rm", "-f", container_name], check=False)

        cmd = [
            "podman",
            "create",
            "--userns=keep-id",
            "--security-opt",
            "label=disable",
            "--name",
            container_name,
            "-v",
            f"{repo_volume}:/workspace",
        ]
        for mount in mounts or []:
            cmd.extend(["-v", mount])
        for key, value in sorted((env or {}).items()):
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(image)

        self._run(cmd)

    def start_agent(self, name: str) -> None:
        self._run(["podman", "start", name])

    def stop_agent(self, name: str) -> None:
        self._run(["podman", "stop", name])

    def remove_agent(self, name: str) -> None:
        self._run(["podman", "rm", name])

    def refresh_agent_auth(self, *, volume_name: str, host_auth_path: str) -> None:
        self._run(
            [
                "podman",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/workspace",
                "-v",
                f"{host_auth_path}:/run/secrets/codex_auth.json:ro",
                "alpine:3.20",
                "sh",
                "-lc",
                (
                    "mkdir -p /workspace/.codex && "
                    "cp /run/secrets/codex_auth.json /workspace/.codex/auth.json && "
                    "chmod 600 /workspace/.codex/auth.json"
                ),
            ]
        )

    def refresh_agent_config(self, *, volume_name: str, host_config_dir: str) -> None:
        self._run(
            [
                "podman",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/workspace",
                "-v",
                f"{host_config_dir}:/run/secrets/master_claude_config:ro",
                "alpine:3.20",
                "sh",
                "-lc",
                (
                    "mkdir -p /workspace/home/.claude && "
                    "cp -r /run/secrets/master_claude_config/. /workspace/home/.claude/ && "
                    "chmod -R go-rwx /workspace/home/.claude/"
                ),
            ]
        )

    def inspect_agent(self, name: str) -> dict[str, Any] | None:
        completed = self._run(["podman", "inspect", "--type", "container", name], check=False)
        if completed.returncode != 0:
            return None
        payload = json.loads(completed.stdout)
        if not payload:
            return None
        return payload[0]

    def tail_logs(self, name: str, lines: int) -> str:
        completed = self._run(["podman", "logs", "--tail", str(lines), name], check=False)
        if completed.returncode != 0:
            return completed.stderr.strip()
        return completed.stdout
