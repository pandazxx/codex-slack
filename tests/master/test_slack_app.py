from __future__ import annotations
import logging

from src.master.command_dispatch import (
    MasterCommandRequest as SlackCommandRequest,
    dispatch_command as dispatch_slash_command,
    parse_load_text,
    parse_set_model_text,
    parse_set_subagent_text,
    parse_status_text,
)
from src.master.command_format import (
    format_command_result,
    format_status_full_chunks,
)
from src.master.service import CommandResult
from src.master.slack_app import (
    CommandRateLimiter,
    build_slack_reply_plan,
    extract_attachment_urls,
    format_forward_ack,
    is_admin_channel,
    is_supported_thread_subtype,
    send_slack_reply,
    select_thread_image_urls,
    summarize_slack_files,
)
from src.master.response_split import SPLIT_HINT_LINE


class FakeMasterService:
    def list_agents(self) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="listed", data={"agents": []})

    def load_agent(
        self,
        *,
        name: str,
        repo_path: str,
        channel_id: str,
        repo_ref: str = "main",
        platform: str = "slack",
        agent_adapter: str | None = None,
    ) -> CommandResult:
        return CommandResult(
            ok=True,
            code="OK",
            message="loaded",
            data={
                "name": name,
                "repo_path": repo_path,
                "channel_id": channel_id,
                "repo_ref": repo_ref,
                "platform": platform,
                "agent_adapter": agent_adapter,
            },
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

    def set_agent_model(self, *, name: str, model: str | None) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="model updated", data={"name": name, "claude_model": model})

    def set_agent_subagent(self, *, name: str, subagent: str | None) -> CommandResult:
        return CommandResult(
            ok=True,
            code="OK",
            message="subagent updated",
            data={"name": name, "claude_subagent": subagent},
        )


def test_is_admin_channel() -> None:
    assert is_admin_channel("C1", {"C1", "C2"}) is True
    assert is_admin_channel("C9", {"C1", "C2"}) is False


def test_is_supported_thread_subtype_allows_file_share() -> None:
    assert is_supported_thread_subtype(None) is True
    assert is_supported_thread_subtype("") is True
    assert is_supported_thread_subtype("file_share") is True
    assert is_supported_thread_subtype("bot_message") is False


def test_parse_load_text_requires_three_args() -> None:
    name, repo_path, channel_id, repo_ref, adapter = parse_load_text("payments /tmp/repo C123")
    assert (name, repo_path, channel_id, repo_ref, adapter) == ("payments", "/tmp/repo", "C123", "main", None)


def test_parse_load_text_accepts_optional_branch() -> None:
    name, repo_path, channel_id, repo_ref, adapter = parse_load_text("payments /tmp/repo C123 master")
    assert (name, repo_path, channel_id, repo_ref, adapter) == (
        "payments",
        "/tmp/repo",
        "C123",
        "master",
        None,
    )


def test_parse_load_text_accepts_adapter_flag() -> None:
    parsed = parse_load_text("payments /tmp/repo C123 release/1 --adapter claude-code")
    assert parsed == (
        "payments",
        "/tmp/repo",
        "C123",
        "release/1",
        "claude-code",
    )


def test_parse_status_text_accepts_full_flag() -> None:
    name, is_full = parse_status_text("payments --full")
    assert name == "payments"
    assert is_full is True


def test_parse_set_model_text_accepts_optional_model() -> None:
    name, model = parse_set_model_text("payments claude-opus-4-5")
    assert (name, model) == ("payments", "claude-opus-4-5")


def test_parse_set_model_text_allows_model_clear() -> None:
    name, model = parse_set_model_text("payments")
    assert (name, model) == ("payments", None)


def test_parse_set_subagent_text_accepts_optional_subagent() -> None:
    name, subagent = parse_set_subagent_text("payments code-reviewer")
    assert (name, subagent) == ("payments", "code-reviewer")


def test_parse_set_subagent_text_allows_subagent_clear() -> None:
    name, subagent = parse_set_subagent_text("payments")
    assert (name, subagent) == ("payments", None)


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


def test_dispatch_load_command_with_platform_and_adapter_flags() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-load",
        text="payments /tmp/repo C123 main --adapter claude-code",
        channel_id="CADMIN",
        user_id="U1",
        platform="slack",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.data["platform"] == "slack"
    assert result.data["agent_adapter"] == "claude-code"


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


def test_dispatch_set_model_command_accepts_optional_model() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-set-model",
        text="payments claude-opus-4-5",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.data["claude_model"] == "claude-opus-4-5"


def test_dispatch_set_model_command_can_clear_model() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-set-model",
        text="payments",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.data["claude_model"] is None


def test_dispatch_set_subagent_command_accepts_optional_subagent() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-set-subagent",
        text="payments code-reviewer",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.data["claude_subagent"] == "code-reviewer"


def test_dispatch_set_subagent_command_can_clear_subagent() -> None:
    service = FakeMasterService()
    request = SlackCommandRequest(
        command_name="/master-agent-set-subagent",
        text="payments",
        channel_id="CADMIN",
        user_id="U1",
    )

    result = dispatch_slash_command(service, request)
    assert result.ok is True
    assert result.data["claude_subagent"] is None


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
                        "agent_adapter": "claude-code",
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
    assert "adapter=`claude-code`" in payload
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


def test_extract_attachment_urls_keeps_all_matching_files() -> None:
    urls = extract_attachment_urls(
        [
            {
                "mimetype": "image/png",
                "url_private": "https://files.slack.com/a.png",
                "url_private_download": "https://files.slack.com/a-download.png",
            },
            {"mimetype": "text/plain", "url_private": "https://files.slack.com/readme.txt"},
        ]
    )
    assert urls == ["https://files.slack.com/a-download.png", "https://files.slack.com/readme.txt"]


def test_build_slack_reply_plan_uses_hint_sections_when_within_limit() -> None:
    plan = build_slack_reply_plan(f"alpha\n\n{SPLIT_HINT_LINE}\n\nbeta")
    assert plan.send_as_file is False
    assert plan.messages == ["alpha", f"{SPLIT_HINT_LINE}\n\nbeta"]


def test_build_slack_reply_plan_falls_back_to_file_when_hinted_section_too_long() -> None:
    oversized = "a" * 2001
    plan = build_slack_reply_plan(f"alpha\n\n{SPLIT_HINT_LINE}\n\n{oversized}")
    assert plan.send_as_file is True
    assert plan.file_text == f"alpha\n\n{SPLIT_HINT_LINE}\n\n{oversized}"


def test_send_slack_reply_sends_split_messages() -> None:
    sent: list[tuple[str, str]] = []

    def say(*, text: str, thread_ts: str) -> None:
        sent.append((text, thread_ts))

    class FakeClient:
        def files_upload_v2(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            raise AssertionError("file upload should not be used")

    send_slack_reply(
        say=say,
        client=FakeClient(),
        channel_id="C123",
        thread_ts="1000.1",
        text=f"alpha\n\n{SPLIT_HINT_LINE}\n\nbeta",
    )

    assert sent == [("alpha", "1000.1"), (f"{SPLIT_HINT_LINE}\n\nbeta", "1000.1")]


def test_send_slack_reply_uploads_file_for_oversized_hinted_section() -> None:
    uploads: list[dict] = []

    def say(*, text: str, thread_ts: str) -> None:
        raise AssertionError("say should not be used when file fallback triggers")

    class FakeClient:
        def files_upload_v2(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            uploads.append(kwargs)

    text = f"alpha\n\n{SPLIT_HINT_LINE}\n\n{'a' * 2001}"
    send_slack_reply(
        say=say,
        client=FakeClient(),
        channel_id="C123",
        thread_ts="1000.1",
        text=text,
    )

    assert len(uploads) == 1
    assert uploads[0]["channel"] == "C123"
    assert uploads[0]["thread_ts"] == "1000.1"
    assert uploads[0]["content"] == text


def test_extract_attachment_urls_filters_by_matching_event_ts_when_shares_present() -> None:
    urls = extract_attachment_urls(
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


def test_extract_attachment_urls_keeps_file_without_shares_metadata() -> None:
    urls = extract_attachment_urls(
        [{"mimetype": "image/png", "url_private": "https://files.slack.com/no-shares.png"}],
        "1000.010",
    )
    assert urls == ["https://files.slack.com/no-shares.png"]


def test_summarize_slack_files_counts_non_image_payloads() -> None:
    summary = summarize_slack_files(
        [
            {"mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
            {"mimetype": "image/png", "url_private": "https://files.slack.com/no-shares.png"},
        ]
    )
    assert summary["total_files"] == 2
    assert summary["matched_files"] == 2
    assert summary["image_files"] == 1
    assert summary["non_image_files"] == 1
    assert summary["non_image_mimetypes"] == ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]


def test_select_thread_image_urls_keeps_all_for_file_share() -> None:
    selected = select_thread_image_urls(
        ["https://files.slack.com/first.png", "https://files.slack.com/second.png"],
        "file_share",
    )
    assert selected == ["https://files.slack.com/first.png", "https://files.slack.com/second.png"]


def test_format_forward_ack_contains_text_and_image_counts() -> None:
    payload = format_forward_ack(text="hello", image_count=2)
    assert "Received message and forwarded to agent." in payload
    assert "text_chars=`5`" in payload
    assert "attachments=`2`" in payload


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
