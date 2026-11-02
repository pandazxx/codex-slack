"""Tests for the mr-deed adapter's master-side surface: the `mr-deed` adapter
value, the per-staff `agent_config` YAML and its delivery in the dispatch payload."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]

_CONFIG = "agent:\n  cost_limit: 1.0\nmodel:\n  model_kwargs:\n    api_key: sk-test-secret\n"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def mqtt_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            yield c, mock_mqtt


def _make_ws_topic(client):
    ws_id = client.post("/api/workspaces", json={"name": "repo", "repo_url": "http://x"}).json()["id"]
    topic_id = client.post(
        f"/api/workspaces/{ws_id}/topics", json={"subject": "t", "repo_ref": "main"}
    ).json()["id"]
    return ws_id, topic_id


def _sent_payload(mock_mqtt) -> dict:
    return json.loads(mock_mqtt.publish.call_args.args[1])


# ── Adapter value and agent_config CRUD ──────────────────────────────────────

def test_mr_deed_adapter_accepted(client):
    r = client.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed"})
    assert r.status_code == 201
    assert r.json()["adapter"] == "mr-deed"
    assert r.json()["agent_config"] is None


def test_agent_config_round_trips(client):
    r = client.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed", "agent_config": _CONFIG})
    assert r.status_code == 201
    assert client.get("/api/staffs").json()[0]["agent_config"] == _CONFIG


def test_agent_config_updated_and_cleared(client):
    client.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed", "agent_config": _CONFIG})
    r = client.put("/api/staffs/deed", json={"name": "deed", "adapter": "mr-deed", "agent_config": "agent: {}\n"})
    assert r.json()["agent_config"] == "agent: {}\n"
    r = client.put("/api/staffs/deed", json={"name": "deed", "adapter": "mr-deed", "agent_config": ""})
    assert r.json()["agent_config"] is None


@pytest.mark.parametrize("bad", ["agent: [unclosed", "- a\n- b\n", "just a string"])
def test_agent_config_must_be_yaml_mapping(client, bad):
    r = client.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed", "agent_config": bad})
    assert r.status_code == 422
    assert "agent_config" in r.json()["detail"]


def test_agent_config_validated_on_update(client):
    client.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed"})
    r = client.put("/api/staffs/deed", json={"name": "deed", "adapter": "mr-deed", "agent_config": "- x\n"})
    assert r.status_code == 422


def test_topic_staff_agent_config_shadows_global(client):
    ws_id, topic_id = _make_ws_topic(client)
    client.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed", "agent_config": "agent: {a: 1}\n"})
    client.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/staffs",
        json={"name": "deed", "adapter": "mr-deed", "agent_config": "agent: {b: 2}\n"},
    )
    staffs = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/staffs").json()
    deed = [s for s in staffs if s["name"] == "deed"]
    assert len(deed) == 1
    assert deed[0]["agent_config"] == "agent: {b: 2}\n"


# ── Dispatch payload ─────────────────────────────────────────────────────────

def test_payload_carries_agent_config(mqtt_client):
    c, mock_mqtt = mqtt_client
    ws_id, topic_id = _make_ws_topic(c)
    c.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed", "agent": "helper", "agent_config": _CONFIG})
    r = c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages", data={"text": "@deed hi"})
    assert r.status_code == 202
    payload = _sent_payload(mock_mqtt)
    assert payload["adapter"] == "mr-deed"
    assert payload["subagent"] == "helper"
    assert payload["agent_config"] == _CONFIG


def test_payload_agent_config_key_present_when_unset(mqtt_client):
    c, mock_mqtt = mqtt_client
    ws_id, topic_id = _make_ws_topic(c)
    c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages", data={"text": "hello"})
    payload = _sent_payload(mock_mqtt)
    assert "agent_config" in payload
    assert payload["agent_config"] is None


def test_stored_transcript_hides_agent_config(mqtt_client):
    c, _ = mqtt_client
    ws_id, topic_id = _make_ws_topic(c)
    c.post("/api/staffs", json={"name": "deed", "adapter": "mr-deed", "agent_config": _CONFIG})
    c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages", data={"text": "@deed hi"})
    msgs = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages").json()
    user_msg = next(m for m in msgs if m["sender"] == "user")
    assert "sk-test-secret" not in json.dumps(msgs)
    assert json.loads(user_msg["transcript"])["agent_config"] == f"<agent_config: {len(_CONFIG)} bytes>"


# ── Alembic revision 000000000002 ──────────────────────────────────────────

def _alembic_config(url: str):
    from alembic.config import Config
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _staff_columns(url: str) -> set[str]:
    from sqlalchemy import create_engine, inspect
    eng = create_engine(url)
    try:
        return {c["name"] for c in inspect(eng).get_columns("staffs")}
    finally:
        eng.dispose()


def test_revision_2_adds_and_drops_agent_config(db_url, monkeypatch):
    """Baseline → head adds staffs.agent_config without touching existing rows;
    downgrading one step removes it again."""
    from alembic import command
    from src.master.db import _reset_engine_cache, get_connection

    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)
    assert "agent_config" in _staff_columns(db_url), "fixture DB must be at head"
    try:
        command.downgrade(cfg, "000000000001")
        _reset_engine_cache()
        assert "agent_config" not in _staff_columns(db_url)

        with get_connection(db_url) as conn:
            conn.execute(
                "INSERT INTO staffs (id, scope_type, scope_id, name, adapter, session_scope,"
                " is_default, created_at, updated_at)"
                " VALUES ('s1', 'global', NULL, 'old', 'codex', 'topic', FALSE, '2026-01-01', '2026-01-01')"
            )
            conn.commit()

        command.upgrade(cfg, "head")
        _reset_engine_cache()
        assert "agent_config" in _staff_columns(db_url)
        with get_connection(db_url) as conn:
            row = conn.execute("SELECT name, adapter, agent_config FROM staffs WHERE id = 's1'").fetchone()
        assert (row["name"], row["adapter"], row["agent_config"]) == ("old", "codex", None)

        command.downgrade(cfg, "000000000001")
        _reset_engine_cache()
        assert "agent_config" not in _staff_columns(db_url)
    finally:
        # Never leave a shared (Postgres CI) schema downgraded.
        command.upgrade(cfg, "head")
        _reset_engine_cache()
