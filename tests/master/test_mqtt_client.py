from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import paho.mqtt.client as mqtt
import pytest
from fastapi.testclient import TestClient

from src.master.config import MasterSettings
from src.master.mqtt_client import (
    _RESPONSE_TOPIC,
    _STATUS_TOPIC,
    _on_connect,
    _on_disconnect,
    _on_message,
    build_client,
)


def _make_settings(**kwargs):
    defaults = dict(
        data_dir="/tmp",
        dry_run=False,
        agent_base_image="img:latest",
        agent_codex_auth_json_path=None,
        agent_ssh_auth_sock_path=None,
        agent_ssh_known_hosts_path=None,
        git_user_name=None,
        git_user_email=None,
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
