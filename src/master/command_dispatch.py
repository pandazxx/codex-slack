from __future__ import annotations

from dataclasses import dataclass
import shlex

from .provisioning import ProvisionContext, ProvisionRequest, ProvisioningCoordinator
from .service import CommandResult, MasterService


@dataclass(frozen=True)
class MasterCommandRequest:
    command_name: str
    text: str
    channel_id: str
    user_id: str
    platform: str = "slack"


def parse_load_text(text: str) -> tuple[str, str, str, str, str | None]:
    parts = [part.strip() for part in text.split() if part.strip()]
    if len(parts) < 3:
        raise ValueError(
            "usage: /master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]"
        )
    name, repo_path, channel_id = parts[0], parts[1], parts[2]
    repo_ref = "main"
    adapter: str | None = None
    idx = 3
    while idx < len(parts):
        token = parts[idx]
        if token == "--adapter":
            if idx + 1 >= len(parts):
                raise ValueError("usage: /master-agent-load ... --adapter <codex|claude-code>")
            adapter = parts[idx + 1].strip().lower()
            idx += 2
            continue
        if token.startswith("--"):
            raise ValueError(f"unknown option: {token}")
        if repo_ref != "main":
            raise ValueError(
                "usage: /master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter codex|claude-code]"
            )
        repo_ref = token
        idx += 1
    return name, repo_path, channel_id, repo_ref, adapter


def parse_provision_text(text: str) -> ProvisionRequest:
    usage = (
        "usage: /master-agent-provision <name> [repo_path] [channel_id] [branch] "
        "[--branch <branch>] "
        "[--create-repo] [--repo-owner <owner>] [--repo-name <name>] [--repo-visibility private|public] "
        "[--create-channel] [--channel-name <name>] [--channel-visibility private|public] "
        "[--adapter codex|claude-code]"
    )
    parts = shlex.split(text)
    if not parts:
        raise ValueError(usage)

    name = parts[0].strip()
    repo_path: str | None = None
    channel_id: str | None = None
    repo_ref = "main"
    agent_adapter: str | None = None
    create_repo = False
    repo_owner: str | None = None
    repo_name: str | None = None
    repo_visibility = "private"
    create_channel = False
    channel_name: str | None = None
    channel_visibility = "private"

    positional: list[str] = []
    idx = 1
    while idx < len(parts):
        token = parts[idx]
        if token == "--create-repo":
            create_repo = True
            idx += 1
            continue
        if token == "--branch":
            if idx + 1 >= len(parts):
                raise ValueError(usage)
            repo_ref = parts[idx + 1].strip()
            idx += 2
            continue
        if token == "--repo-owner":
            if idx + 1 >= len(parts):
                raise ValueError(usage)
            repo_owner = parts[idx + 1].strip()
            idx += 2
            continue
        if token == "--repo-name":
            if idx + 1 >= len(parts):
                raise ValueError(usage)
            repo_name = parts[idx + 1].strip()
            idx += 2
            continue
        if token == "--repo-visibility":
            if idx + 1 >= len(parts):
                raise ValueError(usage)
            repo_visibility = parts[idx + 1].strip().lower()
            idx += 2
            continue
        if token == "--create-channel":
            create_channel = True
            idx += 1
            continue
        if token == "--channel-name":
            if idx + 1 >= len(parts):
                raise ValueError(usage)
            channel_name = parts[idx + 1].strip()
            idx += 2
            continue
        if token == "--channel-visibility":
            if idx + 1 >= len(parts):
                raise ValueError(usage)
            channel_visibility = parts[idx + 1].strip().lower()
            idx += 2
            continue
        if token == "--adapter":
            if idx + 1 >= len(parts):
                raise ValueError(usage)
            agent_adapter = parts[idx + 1].strip().lower()
            idx += 2
            continue
        if token.startswith("--"):
            raise ValueError(f"unknown option: {token}")
        positional.append(token)
        idx += 1

    if len(positional) > 3:
        raise ValueError(usage)
    if positional:
        repo_path = positional[0]
    if len(positional) >= 2:
        channel_id = positional[1]
    if len(positional) >= 3:
        repo_ref = positional[2]

    return ProvisionRequest(
        name=name,
        platform="slack",
        repo_path=repo_path,
        channel_id=channel_id,
        repo_ref=repo_ref,
        agent_adapter=agent_adapter,
        create_repo=create_repo,
        repo_owner=repo_owner,
        repo_name=repo_name,
        repo_visibility=repo_visibility,
        create_channel=create_channel,
        channel_name=channel_name,
        channel_visibility=channel_visibility,
    )


def parse_single_name_text(text: str, command_name: str) -> str:
    name = text.strip()
    if not name:
        raise ValueError(f"usage: {command_name} <name>")
    return name


def parse_optional_name_text(text: str) -> str | None:
    value = text.strip()
    return value or None


def parse_status_text(text: str, command_name: str = "/master-agent-status") -> tuple[str, bool]:
    parts = [part.strip() for part in text.split() if part.strip()]
    if not parts:
        raise ValueError(f"usage: {command_name} <name> [--full]")
    if len(parts) == 1:
        return parts[0], False
    if len(parts) == 2 and parts[1] == "--full":
        return parts[0], True
    raise ValueError(f"usage: {command_name} <name> [--full]")


def parse_set_model_text(text: str, command_name: str = "/master-agent-set-model") -> tuple[str, str | None]:
    parts = [part.strip() for part in text.split(maxsplit=1) if part.strip()]
    if not parts:
        raise ValueError(f"usage: {command_name} <name> [model]")
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1] or None


def dispatch_command(
    service: MasterService,
    request: MasterCommandRequest,
    *,
    provisioning: ProvisioningCoordinator | None = None,
    provision_context: ProvisionContext | None = None,
) -> CommandResult:
    if request.command_name == "/master-agent-list":
        return service.list_agents()

    if request.command_name == "/master-agent-load":
        name, repo_path, channel_id, repo_ref, agent_adapter = parse_load_text(request.text)
        return service.load_agent(
            name=name,
            repo_path=repo_path,
            channel_id=channel_id,
            repo_ref=repo_ref,
            platform=request.platform,
            agent_adapter=agent_adapter,
        )

    if request.command_name == "/master-agent-provision":
        if provisioning is None or provision_context is None:
            return CommandResult(
                ok=False,
                code="ERR_INTERNAL",
                message="provisioning is not configured",
                data={},
            )
        parsed = parse_provision_text(request.text)
        return provisioning.provision_agent(
            ProvisionRequest(
                **{
                    **parsed.__dict__,
                    "platform": request.platform,
                }
            ),
            context=provision_context,
        )

    if request.command_name == "/master-agent-start":
        name = parse_single_name_text(request.text, request.command_name)
        return service.start_agent(name=name)

    if request.command_name == "/master-agent-stop":
        name = parse_single_name_text(request.text, request.command_name)
        return service.stop_agent(name=name)

    if request.command_name == "/master-agent-status":
        name, _ = parse_status_text(request.text)
        return service.status(name=name)

    if request.command_name == "/master-agent-remove":
        name = parse_single_name_text(request.text, request.command_name)
        return service.remove_agent(name=name)

    if request.command_name == "/master-agent-refresh-auth":
        name = parse_single_name_text(request.text, request.command_name)
        return service.refresh_agent_auth(name=name)

    if request.command_name == "/master-agent-refresh-config":
        name = parse_single_name_text(request.text, request.command_name)
        return service.refresh_agent_config(name=name)

    if request.command_name == "/master-agent-set-model":
        name, model = parse_set_model_text(request.text, request.command_name)
        return service.set_agent_model(name=name, model=model)

    return CommandResult(ok=False, code="ERR_INVALID_ARGS", message=f"unsupported command: {request.command_name}", data={})
