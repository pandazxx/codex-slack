from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re

from .registry import AgentRecord, AgentRegistry
from .runtime_adapter import RuntimeAdapter

DEFAULT_IMAGE = "codex-slack-bot:latest"
DEFAULT_RUNTIME = "podman"
LOGGER = logging.getLogger(__name__)


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
        result = CommandResult(ok=True, code="OK", message="agents listed", data={"agents": agents})
        self._audit(command="list", agent="-", result=result)
        return result

    def load_agent(self, *, name: str, repo_path: str, channel_id: str) -> CommandResult:
        validation_error = self._validate_load_inputs(name=name, channel_id=channel_id)
        if validation_error:
            self._audit(command="load", agent=name or "-", result=validation_error)
            return validation_error

        if not Path(repo_path).exists():
            result = CommandResult(
                ok=False,
                code="ERR_REPO_NOT_ALLOWED",
                message=f"repo path does not exist: {repo_path}",
                data={},
            )
            self._audit(command="load", agent=name, result=result)
            return result

        conflict = self._registry.find_by_channel(channel_id)
        if conflict and conflict.name != name:
            result = CommandResult(
                ok=False,
                code="ERR_CHANNEL_CONFLICT",
                message=f"channel {channel_id} already bound to {conflict.name}",
                data={"owner": conflict.name},
            )
            self._audit(command="load", agent=name, result=result)
            return result

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
        result = CommandResult(
            ok=True,
            code="OK",
            message=f"loaded {name}",
            data={
                "state": saved.status,
                "image_plan": saved.image_plan,
                "channel_id": saved.channel_id,
            },
        )
        self._audit(command="load", agent=name, result=result)
        return result

    def start_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="start", agent=name, result=result)
            return result

        try:
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
        except Exception as exc:  # noqa: BLE001
            record.status = "error"
            record.last_error = str(exc)
            self._registry.upsert(record)
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to start {name}: {exc}",
                data={"state": record.status},
            )
            self._audit(command="start", agent=name, result=result)
            return result

        record.status = "running"
        record.resolved_image = image
        record.last_error = None
        self._registry.upsert(record)

        result = CommandResult(
            ok=True,
            code="OK",
            message=f"started {name}",
            data={"state": record.status, "container_name": record.container_name, "resolved_image": image},
        )
        self._audit(command="start", agent=name, result=result)
        return result

    def stop_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="stop", agent=name, result=result)
            return result

        try:
            self._runtime.stop_agent(record.container_name)
        except Exception as exc:  # noqa: BLE001
            record.status = "error"
            record.last_error = str(exc)
            self._registry.upsert(record)
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to stop {name}: {exc}",
                data={"state": record.status},
            )
            self._audit(command="stop", agent=name, result=result)
            return result

        record.status = "stopped"
        record.last_error = None
        self._registry.upsert(record)
        result = CommandResult(ok=True, code="OK", message=f"stopped {name}", data={"state": record.status})
        self._audit(command="stop", agent=name, result=result)
        return result

    def remove_agent(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="remove", agent=name, result=result)
            return result

        try:
            self._runtime.remove_agent(record.container_name)
        except Exception as exc:  # noqa: BLE001
            result = CommandResult(
                ok=False,
                code="ERR_RUNTIME_FAILED",
                message=f"failed to remove {name}: {exc}",
                data={},
            )
            self._audit(command="remove", agent=name, result=result)
            return result

        self._registry.remove(name)
        result = CommandResult(ok=True, code="OK", message=f"removed {name}", data={"removed": True})
        self._audit(command="remove", agent=name, result=result)
        return result

    def status(self, *, name: str) -> CommandResult:
        record = self._registry.get(name)
        if not record:
            result = CommandResult(ok=False, code="ERR_AGENT_NOT_FOUND", message=f"unknown agent: {name}", data={})
            self._audit(command="status", agent=name, result=result)
            return result

        inspect = self._runtime.inspect_agent(record.container_name)
        result = CommandResult(
            ok=True,
            code="OK",
            message=f"status {name}",
            data={"record": record.to_dict(), "runtime": inspect},
        )
        self._audit(command="status", agent=name, result=result)
        return result

    def _validate_load_inputs(self, *, name: str, channel_id: str) -> CommandResult | None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", name):
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="invalid agent name",
                data={"field": "name"},
            )
        if not channel_id.startswith("C"):
            return CommandResult(
                ok=False,
                code="ERR_INVALID_ARGS",
                message="invalid channel id",
                data={"field": "channel_id"},
            )
        return None

    @staticmethod
    def _audit(*, command: str, agent: str, result: CommandResult) -> None:
        LOGGER.info(
            "master.audit command=%s agent=%s ok=%s code=%s message=%s",
            command,
            agent,
            result.ok,
            result.code,
            result.message,
        )

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
