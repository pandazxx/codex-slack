from __future__ import annotations

from src.master.registry import AgentRecord, AgentRegistry


def test_registry_upsert_and_get(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")

    record = AgentRecord(
        name="payments-api",
        repo_path="/tmp/repo",
        channel_id="C123",
        container_name="agent-payments-api",
        runtime="podman",
        image_plan={"type": "default", "image": "codex-slack-bot:latest"},
        status="loaded",
    )
    saved = registry.upsert(record)

    loaded = registry.get("payments-api")
    assert loaded is not None
    assert loaded.name == "payments-api"
    assert loaded.created_at
    assert loaded.updated_at
    assert saved.created_at == loaded.created_at


def test_registry_find_by_channel(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    registry.upsert(
        AgentRecord(
            name="payments-api",
            repo_path="/tmp/repo",
            channel_id="C123",
            container_name="agent-payments-api",
            runtime="podman",
            image_plan={"type": "default", "image": "codex-slack-bot:latest"},
            status="loaded",
        )
    )

    match = registry.find_by_channel("C123")
    assert match is not None
    assert match.name == "payments-api"


def test_registry_creates_lock_file(tmp_path) -> None:
    registry_path = tmp_path / "agents.json"
    registry = AgentRegistry(registry_path)
    _ = registry.list_agents()

    lock_path = registry_path.with_suffix(".json.lock")
    assert lock_path.exists()
