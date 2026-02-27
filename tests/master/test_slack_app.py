from __future__ import annotations

import json

from src.master.service import CommandResult
from src.master.slack_app import (
    CommandRateLimiter,
    SlackCommandRequest,
    dispatch_slash_command,
    format_command_result,
    is_admin_channel,
    parse_load_text,
)


class FakeMasterService:
    def list_agents(self) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="listed", data={"agents": []})

    def load_agent(self, *, name: str, repo_path: str, channel_id: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="loaded", data={"name": name, "repo_path": repo_path, "channel_id": channel_id})

    def start_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="started", data={"name": name})

    def stop_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="stopped", data={"name": name})

    def status(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="status", data={"name": name})

    def remove_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="removed", data={"name": name})


def test_is_admin_channel() -> None:
    assert is_admin_channel("C1", {"C1", "C2"}) is True
    assert is_admin_channel("C9", {"C1", "C2"}) is False


def test_parse_load_text_requires_three_args() -> None:
    name, repo_path, channel_id = parse_load_text("payments /tmp/repo C123")
    assert (name, repo_path, channel_id) == ("payments", "/tmp/repo", "C123")


def test_dispatch_load_command() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-load",
        text="payments /tmp/repo C123",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.data["name"] == "payments"


def test_dispatch_list_command() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-list",
        text="",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.data["agents"] == []


def test_format_command_result_json_payload() -> None:
    payload = format_command_result(
        "/master-agent-list",
        CommandResult(ok=True, code="OK", message="listed", data={"agents": []}),
    )
    obj = json.loads(payload)
    assert obj["ok"] is True
    assert obj["command"] == "/master-agent-list"
    assert obj["code"] == "OK"


def test_command_rate_limiter_blocks_after_limit() -> None:
    limiter = CommandRateLimiter(max_calls=2, window_seconds=60)
    assert limiter.allow("C1:U1") is True
    assert limiter.allow("C1:U1") is True
    assert limiter.allow("C1:U1") is False
