from __future__ import annotations

from src.master.command_runtime import execute_master_command
from src.master.service import CommandResult
from src.master.slack_app import CommandRateLimiter


class FakeService:
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
        return CommandResult(ok=True, code="OK", message="loaded", data={"name": name})

    def start_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="started", data={"name": name})

    def stop_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="stopped", data={"name": name})

    def status(self, *, name: str) -> CommandResult:
        return CommandResult(
            ok=True,
            code="OK",
            message=f"status {name}",
            data={
                "record": {"name": name, "status": "running", "channel_id": "C1", "container_name": "agent-x"},
                "runtime": {"blob": "x" * 7000},
            },
        )

    def remove_agent(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="removed", data={"name": name})

    def refresh_agent_auth(self, *, name: str) -> CommandResult:
        return CommandResult(ok=True, code="OK", message="refreshed", data={"name": name})


class FakeRouter:
    def usage_summary(self, agent_name: str | None) -> list[dict[str, object]]:
        return [
            {
                "agent_name": agent_name or "all",
                "prompt_count": 1,
                "image_count": 0,
                "prompt_chars": 10,
                "response_chars": 20,
                "avg_latency_ms": 123.4,
            }
        ]


def test_execute_master_command_rejects_non_admin_channel() -> None:
    messages = execute_master_command(
        command_name="/master-agent-list",
        text="",
        channel_id="CNO",
        user_id="U1",
        admin_channels={"CADMIN"},
        service=FakeService(),
    )
    assert len(messages) == 1
    assert "admin channel only" in messages[0]


def test_execute_master_command_handles_status_full_chunks() -> None:
    messages = execute_master_command(
        command_name="/master-agent-status",
        text="payments --full",
        channel_id="CADMIN",
        user_id="U1",
        admin_channels={"CADMIN"},
        service=FakeService(),
    )
    assert len(messages) >= 2
    assert "full output" in messages[0]


def test_execute_master_command_handles_usage_with_router() -> None:
    messages = execute_master_command(
        command_name="/master-agent-usage",
        text="payments",
        channel_id="CADMIN",
        user_id="U1",
        admin_channels={"CADMIN"},
        service=FakeService(),
        router=FakeRouter(),  # type: ignore[arg-type]
    )
    assert len(messages) == 1
    assert "bar_chart" in messages[0]


def test_execute_master_command_applies_rate_limit() -> None:
    limiter = CommandRateLimiter(max_calls=1, window_seconds=60)
    first = execute_master_command(
        command_name="/master-agent-list",
        text="",
        channel_id="CADMIN",
        user_id="U1",
        admin_channels={"CADMIN"},
        service=FakeService(),
        rate_limiter=limiter,
    )
    second = execute_master_command(
        command_name="/master-agent-list",
        text="",
        channel_id="CADMIN",
        user_id="U1",
        admin_channels={"CADMIN"},
        service=FakeService(),
        rate_limiter=limiter,
    )
    assert "No agents loaded" in first[0]
    assert "ERR_RATE_LIMITED" in second[0]
