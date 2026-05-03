from __future__ import annotations

import json

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


def test_registry_find_by_channel_is_platform_scoped(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    registry.upsert(
        AgentRecord(
            name="payments-discord",
            repo_path="/tmp/repo",
            channel_id="123456789012345678",
            container_name="agent-payments-discord",
            runtime="podman",
            image_plan={"type": "default", "image": "codex-slack-bot:latest"},
            status="loaded",
            platform="discord",
        )
    )

    assert registry.find_by_channel("123456789012345678", platform="slack") is None
    match = registry.find_by_channel("123456789012345678", platform="discord")
    assert match is not None
    assert match.name == "payments-discord"


def test_registry_creates_lock_file(tmp_path) -> None:
    registry_path = tmp_path / "agents.json"
    registry = AgentRegistry(registry_path)
    _ = registry.list_agents()

    lock_path = registry_path.with_suffix(".json.lock")
    assert lock_path.exists()


def _make_record(name: str = "payments-api", channel_id: str = "C123") -> AgentRecord:
    return AgentRecord(
        name=name,
        repo_path="/tmp/repo",
        channel_id=channel_id,
        container_name=f"agent-{name}",
        runtime="podman",
        image_plan={"type": "default", "image": "codex-slack-bot:latest"},
        status="loaded",
    )


def test_touch_last_message_at_sets_timestamp(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    registry.upsert(_make_record())

    assert registry.get("payments-api").last_message_at is None

    result = registry.touch_last_message_at("payments-api")
    assert result is True

    record = registry.get("payments-api")
    assert record.last_message_at is not None
    assert "T" in record.last_message_at  # ISO 8601


def test_touch_last_message_at_updates_on_each_call(tmp_path) -> None:
    import time

    registry = AgentRegistry(tmp_path / "agents.json")
    registry.upsert(_make_record())

    registry.touch_last_message_at("payments-api")
    first = registry.get("payments-api").last_message_at

    time.sleep(0.01)
    registry.touch_last_message_at("payments-api")
    second = registry.get("payments-api").last_message_at

    assert second >= first  # monotonically non-decreasing


def test_touch_last_message_at_returns_false_for_missing_agent(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    assert registry.touch_last_message_at("nonexistent") is False


def test_last_message_at_defaults_none_on_load(tmp_path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    registry.upsert(_make_record())
    record = registry.get("payments-api")
    assert record.last_message_at is None


def test_registry_migrate_schema_adds_platform_and_adapter_defaults(tmp_path) -> None:
    registry_path = tmp_path / "agents.json"
    registry_path.write_text(
        json.dumps(
            {
                "agents": {
                    "payments": {
                        "name": "payments",
                        "repo_path": "/tmp/repo",
                        "channel_id": "C123",
                        "container_name": "agent-payments",
                        "runtime": "podman",
                        "image_plan": {"type": "default", "image": "codex-slack-bot:latest"},
                        "status": "loaded",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    registry = AgentRegistry(registry_path)
    changed = registry.migrate_schema()
    assert changed is True

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["agents"]["payments"]["platform"] == "slack"
    assert payload["agents"]["payments"]["agent_adapter"] == "codex"
