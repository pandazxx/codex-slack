from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import paho.mqtt.client as mqtt
import pytest
from fastapi.testclient import TestClient

from src.master.config import MasterSettings
from src.master.mqtt_client import (
    _RESPONSE_TOPIC,
    _STATUS_TOPIC,
    _PONG_TOPIC,
    _on_connect,
    _on_disconnect,
    _on_message,
    _record_agent_response,
    build_client,
)


def _make_settings(**kwargs):
    defaults = dict(
        data_dir="/tmp",
        dry_run=False,
        agent_base_image="img:latest",
        agent_network="codex-slack_internal",
        agent_codex_auth_json_path=None,
        agent_ssh_auth_sock_path=None,
        agent_ssh_known_hosts_path=None,
        git_user_name=None,
        git_user_email=None,
        claude_code_oauth_token=None,
        anthropic_api_key=None,
        openai_api_key=None,
        gh_token=None,
        mqtt_host="localhost",
        mqtt_port=1883,
        master_port=8080,
        container_runtime="docker",
    )
    return MasterSettings(**{**defaults, **kwargs})


# --- build_client ---

def test_build_client_calls_connect_async():
    settings = _make_settings(mqtt_host="broker", mqtt_port=1883)
    with patch("src.master.mqtt_client.mqtt.Client") as MockClient:
        instance = MockClient.return_value
        build_client(settings)
        instance.connect_async.assert_called_once_with("broker", 1883, keepalive=60)


def test_build_client_sets_callbacks():
    settings = _make_settings()
    with patch("src.master.mqtt_client.mqtt.Client") as MockClient:
        instance = MockClient.return_value
        build_client(settings)
        assert instance.on_connect == _on_connect
        assert instance.on_disconnect == _on_disconnect
        assert instance.on_message == _on_message


# --- on_connect ---

def test_on_connect_subscribes_on_success():
    client = MagicMock()
    reason_code = MagicMock()
    reason_code.is_failure = False
    _on_connect(client, None, None, reason_code, None)
    calls = client.subscribe.call_args_list
    subscribed_topics = {c.args[0] for c in calls}
    assert _RESPONSE_TOPIC in subscribed_topics
    assert _STATUS_TOPIC in subscribed_topics


def test_on_connect_does_not_subscribe_on_failure():
    client = MagicMock()
    reason_code = MagicMock()
    reason_code.is_failure = True
    _on_connect(client, None, None, reason_code, None)
    client.subscribe.assert_not_called()


def test_on_connect_response_topic_qos1():
    client = MagicMock()
    reason_code = MagicMock()
    reason_code.is_failure = False
    _on_connect(client, None, None, reason_code, None)
    response_calls = [c for c in client.subscribe.call_args_list if c.args[0] == _RESPONSE_TOPIC]
    assert response_calls and response_calls[0].kwargs.get("qos", response_calls[0].args[1] if len(response_calls[0].args) > 1 else None) == 1


def test_on_connect_status_topic_qos0():
    client = MagicMock()
    reason_code = MagicMock()
    reason_code.is_failure = False
    _on_connect(client, None, None, reason_code, None)
    status_calls = [c for c in client.subscribe.call_args_list if c.args[0] == _STATUS_TOPIC]
    assert status_calls and status_calls[0].kwargs.get("qos", status_calls[0].args[1] if len(status_calls[0].args) > 1 else None) == 0


# --- _on_message and _record_agent_response ---

def _make_msg(topic: str, payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.topic = topic
    import json as _json
    msg.payload = _json.dumps(payload).encode()
    return msg


def _make_db(tmp_path) -> str:
    import sqlite3
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY, container_name TEXT,
            last_message_at TEXT, last_dispatched_at TEXT, last_responded_at TEXT
        );
        CREATE TABLE topics (id TEXT PRIMARY KEY, workspace_id TEXT);
        INSERT INTO workspaces VALUES ('ws1', 'c1', '2000-01-01T00:00:00Z', NULL, NULL);
        INSERT INTO topics VALUES ('t1', 'ws1');
    """)
    conn.close()
    return db


def test_on_message_status_does_not_record_response(tmp_path):
    db = _make_db(tmp_path)
    msg = _make_msg("codex-slack/workspace/ws1/topic/t1/status", {"state": "thinking"})
    settings = _make_settings()
    with patch("src.master.mqtt_client._record_agent_response") as mock_rec:
        _on_message(MagicMock(), {"db_path": db, "settings": settings}, msg)
        mock_rec.assert_not_called()


def test_on_message_response_records_response(tmp_path):
    db = _make_db(tmp_path)
    msg = _make_msg("codex-slack/workspace/ws1/topic/t1/response", {"last_response": "done"})
    settings = _make_settings()
    with patch("src.master.mqtt_client._record_agent_response") as mock_rec, \
         patch("src.master.mqtt_client._save_agent_response"), \
         patch("src.master.mqtt_client.notify.notify_reply"):
        _on_message(MagicMock(), {"db_path": db, "settings": settings}, msg)
        mock_rec.assert_called_once_with(db, "t1")


def test_record_agent_response_sets_last_responded_at(tmp_path):
    import sqlite3
    db = _make_db(tmp_path)
    _record_agent_response(db, "t1")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT last_responded_at FROM workspaces WHERE id = 'ws1'").fetchone()
    conn.close()
    assert row[0] is not None
    assert row[0] != "2000-01-01T00:00:00Z"


# --- pong handling ---

def test_pong_resolves_pending_ping(tmp_path):
    """A pong message sets the pending_ping event and records the alive value."""
    import threading
    from types import SimpleNamespace

    db = _make_db(tmp_path)
    message_id = "msg-ping-1"

    event = threading.Event()
    pending = {"event": event, "alive": None}
    app_state = SimpleNamespace(pending_pings={message_id: pending})

    msg = _make_msg(
        "codex-slack/workspace/ws1/topic/t1/pong",
        {"message_id": message_id, "alive": True},
    )
    settings = _make_settings()
    _on_message(MagicMock(), {"db_path": db, "settings": settings, "app_state": app_state}, msg)

    assert event.is_set(), "event should be set after pong"
    assert pending["alive"] is True


def test_pong_not_broadcast_to_websocket(tmp_path):
    """Pong messages must not be forwarded to the WebSocket hub."""
    import threading
    from types import SimpleNamespace

    db = _make_db(tmp_path)
    message_id = "msg-ping-2"

    event = threading.Event()
    pending = {"event": event, "alive": False}
    app_state = SimpleNamespace(pending_pings={message_id: pending})
    hub = MagicMock()

    msg = _make_msg(
        "codex-slack/workspace/ws1/topic/t1/pong",
        {"message_id": message_id, "alive": False},
    )
    settings = _make_settings()
    _on_message(
        MagicMock(),
        {"db_path": db, "settings": settings, "app_state": app_state, "hub": hub, "loop": MagicMock()},
        msg,
    )

    hub.broadcast_threadsafe.assert_not_called()


def test_on_connect_subscribes_to_pong():
    """Master _on_connect must subscribe to the pong wildcard topic."""
    client = MagicMock()
    reason_code = MagicMock()
    reason_code.is_failure = False
    _on_connect(client, None, None, reason_code, None)
    subscribed_topics = {c.args[0] for c in client.subscribe.call_args_list}
    assert _PONG_TOPIC in subscribed_topics


# --- lifespan integration ---

def test_lifespan_starts_and_stops_mqtt_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        from src.master.main import app
        with TestClient(app):
            mock_client.loop_start.assert_called_once()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
