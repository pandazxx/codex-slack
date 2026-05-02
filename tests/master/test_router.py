from __future__ import annotations

import json
import subprocess

import pytest

from src.master.registry import AgentRecord, AgentRegistry
from src.master.router import (
    ChannelRouter,
    ClaudeCodeDispatcher,
    MultiAgentDispatcher,
    PodmanExecDispatcher,
    RouteError,
    RouteSkip,
)


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_prompt(
        self,
        *,
        agent_adapter: str = "codex",
        agent_name: str,
        container_name: str,
        prompt: str,
        platform: str = "slack",
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
        claude_model: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "agent_name": agent_name,
                "agent_adapter": agent_adapter,
                "container_name": container_name,
                "prompt": prompt,
                "platform": platform,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "user_id": user_id,
                "image_urls": image_urls or [],
                "claude_model": claude_model,
            }
        )
        return f"{agent_name}:{prompt}"


def _seed_registry(registry: AgentRegistry) -> None:
    registry.upsert(
        AgentRecord(
            name="payments-agent",
            repo_path="/tmp/repo",
            channel_id="CAGENT",
            container_name="agent-payments",
            runtime="podman",
            image_plan={"type": "default", "image": "codex-slack-bot:latest"},
            status="running",
        )
    )


def test_route_prompt_for_mapped_channel(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    response = router.route_prompt(
        channel_id="CAGENT",
        text="<@U123> hello world",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert response == "payments-agent:hello world"
    assert dispatcher.calls[0]["container_name"] == "agent-payments"
    assert dispatcher.calls[0]["agent_adapter"] == "codex"


def test_route_prompt_includes_image_urls_and_tracks_usage(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    router.route_prompt(
        channel_id="CAGENT",
        text="<@U123> investigate this",
        thread_ts="1730000000.1234",
        user_id="U123",
        image_urls=["https://files.slack.com/abc.png"],
    )

    assert dispatcher.calls[0]["image_urls"] == ["https://files.slack.com/abc.png"]

    usage = router.usage_summary("payments-agent")
    assert usage[0]["prompt_count"] == 1
    assert usage[0]["image_count"] == 1


def test_route_prompt_uses_recorded_claude_adapter(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    record = registry.get("payments-agent")
    assert record is not None
    record.agent_adapter = "claude-code"
    registry.upsert(record)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    router.route_prompt(
        channel_id="CAGENT",
        text="<@U123> hello adapter",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert dispatcher.calls[0]["agent_adapter"] == "claude-code"


def test_route_prompt_allows_image_only_message(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    response = router.route_prompt(
        channel_id="CAGENT",
        text="",
        thread_ts="1730000000.1234",
        user_id="U123",
        image_urls=["https://files.slack.com/only-image.png"],
    )

    assert response == "payments-agent:"
    assert dispatcher.calls[0]["image_urls"] == ["https://files.slack.com/only-image.png"]


def test_route_prompt_rejects_unmapped_channel(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    with pytest.raises(RouteSkip):
        router.route_prompt(channel_id="CUNKNOWN", text="hello", thread_ts=None, user_id="U1")


def test_route_prompt_rejects_admin_channel(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    with pytest.raises(RouteSkip):
        router.route_prompt(channel_id="CADMIN", text="hello", thread_ts=None, user_id="U1")


class FailingDispatcher(FakeDispatcher):
    def send_prompt(
        self,
        *,
        agent_adapter: str = "codex",
        agent_name: str,
        container_name: str,
        prompt: str,
        platform: str = "slack",
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
        image_urls: list[str] | None = None,
        claude_model: str | None = None,
    ) -> str:
        raise RouteError("codex exec failed")


def test_route_prompt_dispatch_failure_raises_route_error(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FailingDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    with pytest.raises(RouteError, match="codex exec failed"):
        router.route_prompt(channel_id="CAGENT", text="<@U123> hi", thread_ts="1730000000.1234", user_id="U123")


def test_thread_tracking_and_mention_dedupe(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    router.track_thread(channel_id="CAGENT", thread_ts="1730000000.9999")
    assert router.is_tracked_thread(channel_id="CAGENT", thread_ts="1730000000.9999") is True

    router.mark_mention_event(channel_id="CAGENT", ts="1730000000.1111")
    assert router.consume_marked_mention_event(channel_id="CAGENT", ts="1730000000.1111") is True
    assert router.consume_marked_mention_event(channel_id="CAGENT", ts="1730000000.1111") is False


def test_route_mention_message_tracks_thread_and_routes(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    response = router.route_mention_message(
        channel_id="CAGENT",
        text="<@U1> hello",
        thread_ts="1730000000.5000",
        event_ts="1730000000.5000",
        user_id="U1",
        image_urls=[],
    )

    assert response == "payments-agent:hello"
    assert router.is_tracked_thread(channel_id="CAGENT", thread_ts="1730000000.5000") is True
    assert router.consume_marked_mention_event(channel_id="CAGENT", ts="1730000000.5000") is True


def test_route_followup_message_requires_tracked_thread_and_not_deduped(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    skipped = router.route_followup_message(
        channel_id="CAGENT",
        text="follow up",
        thread_ts="1730000000.7000",
        event_ts="1730000000.7001",
        user_id="U1",
        image_urls=[],
    )
    assert skipped is None

    router.track_thread(channel_id="CAGENT", thread_ts="1730000000.7000")
    router.mark_mention_event(channel_id="CAGENT", ts="1730000000.7002")
    deduped = router.route_followup_message(
        channel_id="CAGENT",
        text="follow up",
        thread_ts="1730000000.7000",
        event_ts="1730000000.7002",
        user_id="U1",
        image_urls=[],
    )
    assert deduped is None

    routed = router.route_followup_message(
        channel_id="CAGENT",
        text="follow up",
        thread_ts="1730000000.7000",
        event_ts="1730000000.7003",
        user_id="U1",
        image_urls=[],
    )
    assert routed == "payments-agent:follow up"


def test_accept_followup_message_allows_ack_before_dispatch(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    router.track_thread(channel_id="CAGENT", thread_ts="1730000000.7000")

    accepted = router.accept_followup_message(
        channel_id="CAGENT",
        text="follow up",
        thread_ts="1730000000.7000",
        event_ts="1730000000.7003",
        user_id="U1",
        image_urls=[],
    )

    assert accepted is True

    response = router.route_prompt(
        channel_id="CAGENT",
        text="follow up",
        thread_ts="1730000000.7000",
        user_id="U1",
        image_urls=[],
    )

    assert response == "payments-agent:follow up"


def test_thread_tracking_is_platform_scoped(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    router.track_thread(channel_id="shared", thread_ts="1730000000.1", platform="slack")
    assert router.is_tracked_thread(channel_id="shared", thread_ts="1730000000.1", platform="slack") is True
    assert router.is_tracked_thread(channel_id="shared", thread_ts="1730000000.1", platform="discord") is False


def test_thread_tracking_persists_across_router_restarts(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    dispatcher = FakeDispatcher()
    state_path = tmp_path / "thread_state.json"
    router = ChannelRouter(
        registry=registry,
        dispatcher=dispatcher,
        admin_channels={"CADMIN"},
        tracked_threads_path=str(state_path),
    )

    router.track_thread(channel_id="CAGENT", thread_ts="1730000000.9999")

    restarted = ChannelRouter(
        registry=registry,
        dispatcher=dispatcher,
        admin_channels={"CADMIN"},
        tracked_threads_path=str(state_path),
    )
    assert restarted.is_tracked_thread(channel_id="CAGENT", thread_ts="1730000000.9999") is True


def test_claude_dispatcher_persists_created_session_across_restarts(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    state_path = tmp_path / "thread_state.json"
    first_dispatcher = ClaudeCodeDispatcher(command_template="claude -p", state_path=str(state_path))
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps([{"State": {"Running": True, "Status": "running"}}]),
                stderr="",
            )
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    first_dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="first",
        platform="discord",
        channel_id="123456789",
        thread_ts="55555",
        user_id="U123",
    )

    restarted_dispatcher = ClaudeCodeDispatcher(command_template="claude -p", state_path=str(state_path))
    restarted_dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="second",
        platform="discord",
        channel_id="123456789",
        thread_ts="99999",
        user_id="U123",
    )

    assert " claude -n " in f" {' '.join(seen[0])} "
    assert " claude -r " in f" {' '.join(seen[1])} "
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["claude_channel_sessions"]["payments-agent:discord:123456789"] == {
        "created": True,
        "session_name": "claude-payments-agent-discord-123456789",
    }


def test_claude_dispatcher_session_persistence_preserves_tracked_threads(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    registry = AgentRegistry(tmp_path / "agents.json")
    state_path = tmp_path / "thread_state.json"
    router = ChannelRouter(
        registry=registry,
        dispatcher=FakeDispatcher(),
        admin_channels={"CADMIN"},
        tracked_threads_path=str(state_path),
    )
    router.track_thread(channel_id="CAGENT", thread_ts="1730000000.9999")

    dispatcher = ClaudeCodeDispatcher(command_template="claude -p", state_path=str(state_path))

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps([{"State": {"Running": True, "Status": "running"}}]),
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    restarted_router = ChannelRouter(
        registry=registry,
        dispatcher=FakeDispatcher(),
        admin_channels={"CADMIN"},
        tracked_threads_path=str(state_path),
    )
    assert restarted_router.is_tracked_thread(channel_id="CAGENT", thread_ts="1730000000.9999") is True


def test_podman_exec_dispatcher_includes_exit_and_output_details(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher()

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        if args[0][:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=17,
            stdout="partial stdout",
            stderr="fatal stderr",
        )

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    with pytest.raises(RouteError, match=r"exit=17"):
        dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="hello",
            channel_id="CAGENT",
            thread_ts="1730000000.1234",
            user_id="U123",
        )


def test_podman_exec_dispatcher_reports_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher(timeout_seconds=12)

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        if args[0][:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=12)

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    with pytest.raises(RouteError, match=r"timed out after 12s"):
        dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="hello",
            channel_id="CAGENT",
            thread_ts="1730000000.1234",
            user_id="U123",
        )


def test_podman_exec_dispatcher_reports_missing_podman(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher()

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("podman")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    with pytest.raises(RouteError, match=r"podman CLI is not available"):
        dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="hello",
            channel_id="CAGENT",
            thread_ts="1730000000.1234",
            user_id="U123",
        )


def test_podman_exec_dispatcher_runs_in_repo_workdir(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher()
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    response = dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert response == "ok"
    assert seen["cmd"][0:7] == ["podman", "exec", "-i", "-e", "CODEX_HOME=/workspace/home/.codex", "--workdir", "/workspace/repo"]
    assert seen["cmd"][-1] == "codex exec --dangerously-bypass-approvals-and-sandbox resume --last -"


def test_podman_exec_dispatcher_injects_session_resume_for_legacy_template(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher(command_template="codex exec --dangerously-bypass-approvals-and-sandbox -")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert seen["cmd"][-1] == (
        "codex exec --dangerously-bypass-approvals-and-sandbox "
        "resume slack-CAGENT-1730000000-1234 -"
    )


def test_podman_exec_dispatcher_does_not_inject_resume_for_last_template(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher(command_template="codex exec --dangerously-bypass-approvals-and-sandbox resume --last -")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert seen["cmd"][-1] == "codex exec --dangerously-bypass-approvals-and-sandbox resume --last -"

def test_multi_agent_dispatcher_selects_adapter_by_name() -> None:
    codex = FakeDispatcher()
    claude = FakeDispatcher()
    dispatcher = MultiAgentDispatcher(dispatchers={"codex": codex, "claude-code": claude}, default_adapter="codex")

    response = dispatcher.send_prompt(
        agent_adapter="claude-code",
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert response == "payments-agent:hello"
    assert len(codex.calls) == 0
    assert len(claude.calls) == 1


def test_podman_exec_dispatcher_uses_master_start_callback_when_container_is_not_running(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    started: list[str] = []
    dispatcher = PodmanExecDispatcher(agent_prepare_callback=started.append)
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(cmd)
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    response = dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert response == "ok"
    assert started == ["payments-agent"]
    assert seen[0] == ["podman", "inspect", "--type", "container", "agent-payments"]
    assert seen[1][0:3] == ["podman", "exec", "-i"]


def test_podman_exec_dispatcher_reports_missing_container(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher()

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(args=cmd, returncode=125, stdout="", stderr="no such container")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    with pytest.raises(RouteError, match=r"agent container is not running and auto-start is not configured: agent-payments"):
        dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="hello",
            channel_id="CAGENT",
            thread_ts="1730000000.1234",
            user_id="U123",
        )


def test_podman_exec_dispatcher_reports_stopped_container_without_callback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher()

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":false,"Status":"exited"}}]',
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    with pytest.raises(RouteError, match=r"agent container is not running and auto-start is not configured: agent-payments"):
        dispatcher.send_prompt(
            agent_name="payments-agent",
            container_name="agent-payments",
            prompt="hello",
            channel_id="CAGENT",
            thread_ts="1730000000.1234",
            user_id="U123",
        )


def test_podman_exec_dispatcher_uses_master_start_callback_when_container_is_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    started: list[str] = []
    dispatcher = PodmanExecDispatcher(agent_prepare_callback=started.append)

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    response = dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert response == "ok"
    assert started == ["payments-agent"]


def test_claude_dispatcher_creates_session_and_injects_permission_bypass(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert seen["cmd"][-1].startswith("claude ")
    assert " -n " in seen["cmd"][-1]
    assert " --session-id " not in seen["cmd"][-1]
    assert " --continue" not in seen["cmd"][-1]
    assert " --dangerously-skip-permissions" in seen["cmd"][-1]


def test_claude_dispatcher_resumes_with_same_session_for_same_channel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="first",
        platform="discord",
        channel_id="123456789",
        thread_ts="55555",
        user_id="U123",
    )
    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="second",
        platform="discord",
        channel_id="123456789",
        thread_ts="99999",
        user_id="U123",
    )

    assert " -n " in seen[0][-1]
    assert " -r " in seen[1][-1]
    assert "claude-payments-agent-discord-123456789" in seen[0][-1]
    assert "claude-payments-agent-discord-123456789" in seen[1][-1]


def test_claude_dispatcher_uses_distinct_sessions_for_distinct_channels(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000000.0001",
        user_id="U123",
    )
    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello again",
        platform="slack",
        channel_id="COTHER",
        thread_ts="1730000000.0002",
        user_id="U123",
    )

    assert "claude-payments-agent-slack-CAGENT" in seen[0][-1]
    assert "claude-payments-agent-slack-COTHER" in seen[1][-1]


def test_claude_dispatcher_retries_with_create_when_session_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p")
    seen: list[list[str]] = []
    calls = {"count": 0}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen.append(cmd)
        calls["count"] += 1
        if calls["count"] == 2:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="Error: no session found for resume.",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    first = dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )
    response = dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="again",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000000.5678",
        user_id="U123",
    )

    assert first == "ok"
    assert response == "ok"
    assert " -n " in seen[0][-1]
    assert " -r " in seen[1][-1]
    assert " -n " in seen[2][-1]


def test_podman_exec_dispatcher_injects_claude_permission_bypass(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher(command_template="claude -p")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert seen["cmd"][-1] == "claude -p --dangerously-skip-permissions"


def test_podman_exec_dispatcher_preserves_existing_claude_permission_bypass(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher(command_template="claude -p --dangerously-skip-permissions --session-id {session_id}")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:4] == ["podman", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='[{"State":{"Running":true,"Status":"running"}}]',
                stderr="",
            )
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert seen["cmd"][-1].count("--dangerously-skip-permissions") == 1
