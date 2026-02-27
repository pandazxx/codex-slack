from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class RuntimeErrorAdapter(RuntimeError):
    pass


class RuntimeAdapter(Protocol):
    def build_image(self, *, name: str, repo_path: str, context_rel: str, dockerfile_rel: str) -> str:
        ...

    def create_or_update_agent(self, *, container_name: str, image: str, repo_volume: str) -> None:
        ...

    def start_agent(self, name: str) -> None:
        ...

    def stop_agent(self, name: str) -> None:
        ...

    def remove_agent(self, name: str) -> None:
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

    def create_or_update_agent(self, *, container_name: str, image: str, repo_volume: str) -> None:
        if self._container_exists(container_name):
            return

        self._run(
            [
                "podman",
                "create",
                "--name",
                container_name,
                "-v",
                f"{repo_volume}:/workspace",
                image,
            ]
        )

    def start_agent(self, name: str) -> None:
        self._run(["podman", "start", name])

    def stop_agent(self, name: str) -> None:
        self._run(["podman", "stop", name])

    def remove_agent(self, name: str) -> None:
        self._run(["podman", "rm", name])

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
