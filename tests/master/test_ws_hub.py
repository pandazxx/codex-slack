from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.mqtt_client import _parse_mqtt_topic
from src.master.ws_hub import ConnectionHub


# --- _parse_mqtt_topic ---

def test_parse_mqtt_topic_response():
    assert _parse_mqtt_topic("codex-slack/workspace/w1/topic/t1/response") == ("t1", "response")


def test_parse_mqtt_topic_status():
    assert _parse_mqtt_topic("codex-slack/workspace/w1/topic/t1/status") == ("t1", "status")


def test_parse_mqtt_topic_malformed():
    assert _parse_mqtt_topic("too/short") is None
    assert _parse_mqtt_topic("a/b/c/d/e/f/g") is None


# --- ConnectionHub (sync wrappers via asyncio.run) ---

def test_hub_broadcast_sends_to_connected_ws():
    hub = ConnectionHub()
    ws = AsyncMock()
    hub.connect("t1", ws)
    asyncio.run(hub.broadcast("t1", {"type": "status", "state": "thinking"}))
    ws.send_json.assert_awaited_once_with({"type": "status", "state": "thinking"})


def test_hub_broadcast_no_connections_is_noop():
    hub = ConnectionHub()
    asyncio.run(hub.broadcast("t1", {"type": "status", "state": "idle"}))


def test_hub_broadcast_skips_other_topics():
    hub = ConnectionHub()
    ws1, ws2 = AsyncMock(), AsyncMock()
    hub.connect("t1", ws1)
    hub.connect("t2", ws2)
    asyncio.run(hub.broadcast("t1", {"type": "status", "state": "thinking"}))
    ws1.send_json.assert_awaited_once()
    ws2.send_json.assert_not_awaited()


def test_hub_disconnect_removes_ws():
    hub = ConnectionHub()
    ws = AsyncMock()
    hub.connect("t1", ws)
    hub.disconnect("t1", ws)
    asyncio.run(hub.broadcast("t1", {"type": "status", "state": "idle"}))
    ws.send_json.assert_not_awaited()


def test_hub_broadcast_removes_dead_connections():
    hub = ConnectionHub()
    ws = AsyncMock()
    ws.send_json.side_effect = Exception("closed")
    hub.connect("t1", ws)
    asyncio.run(hub.broadcast("t1", {"type": "status", "state": "idle"}))
    assert ws not in hub._connections["t1"]


# --- WebSocket endpoint ---

@pytest.fixture()
def ws_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_build.return_value = MagicMock()
        from src.master.main import app
        with TestClient(app) as client:
            yield client, app


def test_ws_endpoint_accepts_connection(ws_client):
    client, app = ws_client
    with client.websocket_connect("/ws/events"):
        pass  # accepted and cleanly disconnected


def test_ws_endpoint_receives_broadcast(ws_client):
    client, app = ws_client
    with client.websocket_connect("/ws/events") as ws:
        msg = {"type": "status", "state": "thinking", "topic_id": "topic-abc"}
        asyncio.run(app.state.hub.broadcast("_global", msg))
        data = ws.receive_json()
        assert data == msg
