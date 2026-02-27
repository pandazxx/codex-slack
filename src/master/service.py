from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .registry import AgentRecord, AgentRegistry
from .runtime_adapter import RuntimeAdapter

DEFAULT_IMAGE = "codex-slack-bot:latest"
DEFAULT_RUNTIME = "podman"


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    code: str
    message: str
    data: dict[str, object]


class MasterService:
    def __init__(self, registry: AgentRegistry, runtime: RuntimeAdapter) -> None:
        self._registry = registry
        self._runtime = runtime

    def list_agents(self) -> CommandResult:
        agents = [agent.to_dict() for agent in self._registry.list_agents()]
        return CommandResult(ok=True, code="OK", message="agents listed", data={"agents": agents})

    def load_agent(self, *, name: str, repo_path: str, channel_id: str) -> CommandResult:
        self._validate_name(name)
        self._validate_channel(channel_id)

        if not Path(repo_path).exists():
            return CommandResult(
                ok=False,
                code="ERR_REPO_NOT_ALLOWED",
                message=f"repo path does not exist: {repo_path}",
                data={},
            )

        conflict = self._registry.find_by_channel(channel_id)
        if conflict and conflict.name != name:
            return CommandResult(
                ok=False,
                code="ERR_CHANNEL_CONFLICT",
                message=f"channel {channel_id} already bound to {conflict.name}",
                data={"owner": conflict.name},
            )

        image_plan = self._resolve_image_plan(repo_path)
        record = self._registry.get(name)
        if not record:
            record = AgentRecord(
                name=name,
                repo_path=repo_path,
                channel_id=channel_id,
                container_name=f"agent-{name}",
                runtime=DEFAULT_RUNTIME,
                image_plan=image_plan,
                status="loaded",
            )
        else:
            record.repo_path = repo_path
            record.channel_id = channel_id
            record.image_plan = image_plan
            record.status = "loaded"
            record.last_error = None

        saved = self._registry.upsert(record)
        return CommandResult(
            ok=True,
            code="OK",
            message=f"loaded {name}",
            data={
                "state": saved.status,
                "image_plan": saved.image_plan,
                "channel_id": saved.channel_id,
            },
        )

    def start_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            return CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})

        image = DEFAULT_IMAGE
        if record.image_plan.get("type") == "dockerfile":
            image = self._runtime.build_image(
                name=record.name,
                repo_path=record.repo_path,
                context_rel=str(record.image_plan["context"]),
                dockerfile_rel=str(record.image_plan["dockerfile"]),
            )

        self._runtime.create_or_update_agent(
            container_name=record.container_name,
            image=image,
            repo_volume=f"agent-workspace-{record.name}",
        )
        self._runtime.start_agent(record.container_name)

        record.status = "running"
        record.resolved_image = image
        record.last_error = None
        self._registry.upsert(record)

        return CommandResult(
            ok=True,
            code="OK",
            message=f"started {name}",
            data={"state": record.status, "container_name": record.container_name, "resolved_image": image},
        )

    def stop_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            return CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})

        self._runtime.stop_agent(record.container_name)
        record.status = "stopped"
        self._registry.upsert(record)
        return CommandResult(ok=True, code="OK", message=f"stopped {name}", data={"state": record.status})

    def remove_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            return CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})

        self._runtime.remove_agent(record.container_name)
        self._registry.remove(name)
        return CommandResult(ok=True, code="OK", message=f"removed {name}", data={"removed": True})

    def status(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            return CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})

        inspect = self._runtime.inspect_agent(record.container_name)
        return CommandResult(
            ok=True,
            code="OK",
            message=f"status {name}",
            data={"record": record.to_dict(), "runtime": inspect},
        )

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or len(name) < 2:
            raise ValueError("invalid agent name")
        if not all(ch.islower() or ch.isdigit() or ch == "-" for ch in name):
            raise ValueError("invalid agent name")

    @staticmethod
    def _validate_channel(channel_id: str) -> None:
        if not channel_id.startswith("C"):
            raise ValueError("invalid channel id")

    @staticmethod
    def _resolve_image_plan(repo_path: str) -> dict[str, str]:
        dockerfile = Path(repo_path) / ".prj_assistant" / "image" / "Dockerfile"
        if dockerfile.exists():
            return {
                "type": "dockerfile",
                "dockerfile": ".prj_assistant/image/Dockerfile",
                "context": ".prj_assistant/image",
            }
        return {"type": "default", "image": DEFAULT_IMAGE}
