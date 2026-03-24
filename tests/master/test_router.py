from __future__ import annotations

import subprocess

import pytest

from src.master.registry import AgentRecord, AgentRegistry
from src.master.dispatch_result import DispatchResult
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
            }
        )
        return DispatchResult(text=f"{agent_name}:{prompt}")


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


def test_podman_exec_dispatcher_includes_exit_and_output_details(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher()

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
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
    assert seen["cmd"][0:7] == ["podman", "exec", "-i", "-e", "CODEX_HOME=/workspace/.codex", "--workdir", "/workspace/repo"]
    assert seen["cmd"][-1] == "codex exec --dangerously-bypass-approvals-and-sandbox resume --last -"


def test_podman_exec_dispatcher_injects_session_resume_for_legacy_template(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher(command_template="codex exec --dangerously-bypass-approvals-and-sandbox -")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
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


def test_claude_dispatcher_adds_stable_session_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
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

    assert seen["cmd"][-1].startswith("claude -p ")
    assert " --session-id " in seen["cmd"][-1]
    assert " --dangerously-skip-permissions" in seen["cmd"][-1]


def test_claude_dispatcher_uses_same_session_id_for_same_channel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
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

    assert " --session-id " in seen[0][-1]
    assert " --resume " in seen[1][-1]


def test_claude_dispatcher_persists_known_channel_sessions(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    state_path = tmp_path / "claude_session_state.json"
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    first = ClaudeCodeDispatcher(command_template="claude -p", session_state_path=str(state_path))
    first.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="first",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    restarted = ClaudeCodeDispatcher(command_template="claude -p", session_state_path=str(state_path))
    restarted.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="second",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000001.1234",
        user_id="U123",
    )

    assert " --session-id " in seen[0][-1]
    assert " --resume " in seen[1][-1]


def test_claude_dispatcher_retries_resume_when_session_already_exists(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p")
    seen: list[list[str]] = []
    calls = {"count": 0}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(cmd)
        calls["count"] += 1
        if calls["count"] == 1:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="Error: Session ID 28113085-b94e-527f-83c9-cce8e88db504 is already in use.",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.master.router.subprocess.run", fake_run)

    response = dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="hello",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000000.1234",
        user_id="U123",
    )

    assert response == "ok"
    assert " --session-id " in seen[0][-1]
    assert " --resume " in seen[1][-1]


def test_claude_dispatcher_respects_explicit_session_placeholder(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = ClaudeCodeDispatcher(command_template="claude -p --session-id {session_id}")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(cmd)
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
    dispatcher.send_prompt(
        agent_name="payments-agent",
        container_name="agent-payments",
        prompt="second",
        platform="slack",
        channel_id="CAGENT",
        thread_ts="1730000001.1234",
        user_id="U123",
    )

    assert seen[0][-1].count("--session-id") == 1
    assert " --resume " in seen[1][-1]


def test_podman_exec_dispatcher_injects_claude_permission_bypass(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dispatcher = PodmanExecDispatcher(command_template="claude -p")
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
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
