from __future__ import annotations

from dataclasses import dataclass

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


def dispatch_command(service: MasterService, request: MasterCommandRequest) -> CommandResult:
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

    return CommandResult(ok=False, code="ERR_INVALID_ARGS", message=f"unsupported command: {request.command_name}", data={})
