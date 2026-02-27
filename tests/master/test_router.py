from __future__ import annotations

import pytest

from src.master.registry import AgentRecord, AgentRegistry
from src.master.router import ChannelRouter, RouteError


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

    with pytest.raises(RouteError):
        router.route_prompt(channel_id="CUNKNOWN", text="hello", thread_ts=None, user_id="U1")


def test_route_prompt_rejects_admin_channel(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    _seed_registry(registry)
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    with pytest.raises(RouteError):
        router.route_prompt(channel_id="CADMIN", text="hello", thread_ts=None, user_id="U1")


def test_thread_tracking_and_mention_dedupe(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    dispatcher = FakeDispatcher()
    router = ChannelRouter(registry=registry, dispatcher=dispatcher, admin_channels={"CADMIN"})

    router.track_thread(channel_id="CAGENT", thread_ts="1730000000.9999")
    assert router.is_tracked_thread(channel_id="CAGENT", thread_ts="1730000000.9999") is True

    router.mark_mention_event(channel_id="CAGENT", ts="1730000000.1111")
    assert router.consume_marked_mention_event(channel_id="CAGENT", ts="1730000000.1111") is True
    assert router.consume_marked_mention_event(channel_id="CAGENT", ts="1730000000.1111") is False
