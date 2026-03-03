from __future__ import annotations

import subprocess

import pytest

from src.master.registry import AgentRecord, AgentRegistry
from src.master.router import ChannelRouter, PodmanExecDispatcher, RouteError, RouteSkip


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def send_prompt(
        self,
        *,
        agent_name: str,
        container_name: str,
        prompt: str,
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
    ) -> str:
        self.calls.append(
            {
                "agent_name": agent_name,
                "container_name": container_name,
                "prompt": prompt,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "user_id": user_id,
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
        agent_name: str,
        container_name: str,
        prompt: str,
        channel_id: str,
        thread_ts: str | None,
        user_id: str | None,
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
