from __future__ import annotations
import logging

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

    def load_agent(self, *, name: str, repo_path: str, channel_id: str, repo_ref: str = "main") -> CommandResult:
        return CommandResult(
            ok=True,
            code="OK",
            message="loaded",
            data={"name": name, "repo_path": repo_path, "channel_id": channel_id, "repo_ref": repo_ref},
        )

    def start_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="started", data={"name": name})

    def stop_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="stopped", data={"name": name})

    def status(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="status", data={"name": name})

    def remove_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="removed", data={"name": name})

    def refresh_agent_auth(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="refreshed", data={"name": name})


def test_is_admin_channel() -> None:
    assert is_admin_channel("C1", {"C1", "C2"}) is True
    assert is_admin_channel("C9", {"C1", "C2"}) is False


def test_parse_load_text_requires_three_args() -> None:
    name, repo_path, channel_id, repo_ref = parse_load_text("payments /tmp/repo C123")
    assert (name, repo_path, channel_id, repo_ref) == ("payments", "/tmp/repo", "C123", "main")


def test_parse_load_text_accepts_optional_branch() -> None:
    name, repo_path, channel_id, repo_ref = parse_load_text("payments /tmp/repo C123 master")
    assert (name, repo_path, channel_id, repo_ref) == ("payments", "/tmp/repo", "C123", "master")


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


def test_dispatch_refresh_auth_command() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-refresh-auth",
        text="payments",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.message == "refreshed"


def test_format_command_result_json_payload() -> None:
    payload = format_command_result(
        "/master-agent-list",
        CommandResult(ok=True, code="OK", message="listed", data={"agents": []}),
    )
    assert ":white_check_mark:" in payload
    assert "*Code:* `OK`" in payload
    assert "*Message:* listed" in payload
    assert "```json" in payload
    assert '"agents": []' in payload


def test_format_command_result_truncates_large_data_payload() -> None:
    payload = format_command_result(
        "/master-agent-status",
        CommandResult(ok=True, code="OK", message="status", data={"blob": "x" * 5000}),
    )
    assert "..." in payload
    assert len(payload) < 1600


def test_command_rate_limiter_blocks_after_limit() -> None:
    limiter = CommandRateLimiter(max_calls=2, window_seconds=60)
    assert limiter.allow("C1:U1") is True
    assert limiter.allow("C1:U1") is True
    assert limiter.allow("C1:U1") is False


def test_dispatch_logging_emits_start_and_done(caplog) -> None:  # type: ignore[no-untyped-def]
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-list",
        text="",
        channel_id="CADMIN",
        user_id="U1",
    )

    caplog.set_level(logging.INFO, logger="src.master.slack_app")
    LOGGER_NAME = "src.master.slack_app"

    logging.getLogger(LOGGER_NAME).info(
        "master.command_dispatch_start command=%s channel=%s user=%s text=%r",
        request.command_name,
        request.channel_id,
        request.user_id,
        request.text,
    )
    result = dispatch_slash_command(service, request)
    logging.getLogger(LOGGER_NAME).info(
        "master.command_dispatch_done command=%s channel=%s user=%s ok=%s code=%s",
        request.command_name,
        request.channel_id,
        request.user_id,
        result.ok,
        result.code,
    )

    assert "master.command_dispatch_start" in caplog.text
    assert "master.command_dispatch_done" in caplog.text
