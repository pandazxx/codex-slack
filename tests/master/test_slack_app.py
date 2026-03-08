from __future__ import annotations
import logging

from src.master.service import CommandResult
from src.master.slack_app import (
    CommandRateLimiter,
    SlackCommandRequest,
    dispatch_slash_command,
    extract_image_urls,
    format_status_full_chunks,
    format_command_result,
    is_admin_channel,
    is_supported_thread_subtype,
    parse_load_text,
    parse_status_text,
    select_thread_image_urls,
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


def test_is_supported_thread_subtype_allows_file_share() -> None:
    assert is_supported_thread_subtype(None) is True
    assert is_supported_thread_subtype("") is True
    assert is_supported_thread_subtype("file_share") is True
    assert is_supported_thread_subtype("bot_message") is False


def test_parse_load_text_requires_three_args() -> None:
    name, repo_path, channel_id, repo_ref = parse_load_text("payments /tmp/repo C123")
    assert (name, repo_path, channel_id, repo_ref) == ("payments", "/tmp/repo", "C123", "main")


def test_parse_load_text_accepts_optional_branch() -> None:
    name, repo_path, channel_id, repo_ref = parse_load_text("payments /tmp/repo C123 master")
    assert (name, repo_path, channel_id, repo_ref) == ("payments", "/tmp/repo", "C123", "master")


def test_parse_status_text_accepts_full_flag() -> None:
    name, is_full = parse_status_text("payments --full")
    assert name == "payments"
    assert is_full is True


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


def test_dispatch_status_command_accepts_full_flag() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-status",
        text="payments --full",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.message == "status"


def test_format_command_result_json_payload() -> None:
    payload = format_command_result(
        "/master-agent-start",
        CommandResult(ok=True, code="OK", message="started", data={"name": "payments-agent"}),
    )
    assert ":white_check_mark:" in payload
    assert "*Code:* `OK`" in payload
    assert "*Message:* started" in payload
    assert "```json" in payload
    assert '"name": "payments-agent"' in payload


def test_format_command_result_status_summary() -> None:
    payload = format_command_result(
        "/master-agent-status",
        CommandResult(
            ok=True,
            code="OK",
            message="status payments",
            data={
                "record": {
                    "name": "payments",
                    "status": "running",
                    "channel_id": "C123",
                    "container_name": "agent-payments",
                },
                "runtime": {"State": {"Status": "running"}},
            },
        ),
    )
    assert "*Agent:* `payments`" in payload
    assert "*State:* registry=`running` runtime=`running`" in payload


def test_format_command_result_keeps_large_data_for_non_status_commands() -> None:
    payload = format_command_result(
        "/master-agent-start",
        CommandResult(ok=True, code="OK", message="started", data={"blob": "x" * 5000}),
    )
    assert "..." not in payload
    assert len(payload) > 3000


def test_format_command_result_renders_agent_list_as_bullets() -> None:
    payload = format_command_result(
        "/master-agent-list",
        CommandResult(
            ok=True,
            code="OK",
            message="agents listed",
            data={
                "agents": [
                    {
                        "name": "aidotfile-agent",
                        "status": "running",
                        "channel_id": "C0123456789",
                        "repo_ref": "master",
                        "runtime": "podman",
                        "container_name": "agent-aidotfile-agent",
                    }
                ]
            },
        ),
    )
    assert "*Total agents:* 1" in payload
    assert "• *aidotfile-agent*" in payload
    assert "state=`running`" in payload
    assert "aidotfile-agent" in payload
    assert "container=`agent-aidotfile-agent`" in payload


def test_format_command_result_renders_usage_bullets() -> None:
    payload = format_command_result(
        "/master-agent-usage",
        CommandResult(
            ok=True,
            code="OK",
            message="usage listed",
            data={
                "usage": [
                    {
                        "agent_name": "aidotfile-agent",
                        "prompt_count": 3,
                        "image_count": 1,
                        "prompt_chars": 1200,
                        "response_chars": 2400,
                        "avg_latency_ms": 412.5,
                    }
                ]
            },
        ),
    )
    assert ":bar_chart: */master-agent-usage*" in payload
    assert "prompts=`3`" in payload
    assert "images=`1`" in payload


def test_status_full_messages_chunk_large_payload() -> None:
    result = CommandResult(
        ok=True,
        code="OK",
        message="status payments",
        data={
            "record": {
                "name": "payments",
                "status": "running",
                "channel_id": "C123",
                "container_name": "agent-payments",
            },
            "runtime": {"blob": "x" * 7000},
        },
    )
    messages = format_status_full_chunks("/master-agent-status", result)
    assert len(messages) >= 2
    assert "full output" in messages[0]
    assert "part 1/" in messages[0]


def test_extract_image_urls_only_keeps_image_files() -> None:
    urls = extract_image_urls(
        [
            {
                "mimetype": "image/png",
                "url_private": "https://files.slack.com/a.png",
                "url_private_download": "https://files.slack.com/a-download.png",
            },
            {"mimetype": "text/plain", "url_private": "https://files.slack.com/readme.txt"},
        ]
    )
    assert urls == ["https://files.slack.com/a-download.png"]


def test_extract_image_urls_filters_by_matching_event_ts_when_shares_present() -> None:
    urls = extract_image_urls(
        [
            {
                "mimetype": "image/png",
                "url_private": "https://files.slack.com/a.png",
                "shares": {
                    "private": {
                        "C123": [
                            {"ts": "1000.001"},
                        ]
                    }
                },
            },
            {
                "mimetype": "image/png",
                "url_private": "https://files.slack.com/b.png",
                "shares": {
                    "private": {
                        "C123": [
                            {"ts": "1000.002"},
                        ]
                    }
                },
            },
        ],
        "1000.002",
    )
    assert urls == ["https://files.slack.com/b.png"]


def test_extract_image_urls_keeps_file_without_shares_metadata() -> None:
    urls = extract_image_urls(
        [{"mimetype": "image/png", "url_private": "https://files.slack.com/no-shares.png"}],
        "1000.010",
    )
    assert urls == ["https://files.slack.com/no-shares.png"]


def test_select_thread_image_urls_keeps_all_for_file_share() -> None:
    selected = select_thread_image_urls(
        ["https://files.slack.com/first.png", "https://files.slack.com/second.png"],
        "file_share",
    )
    assert selected == ["https://files.slack.com/first.png", "https://files.slack.com/second.png"]


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
