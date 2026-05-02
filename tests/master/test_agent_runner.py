from __future__ import annotations

from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from src.master.agent_runner import container_name, spawn_agent, stop_agent


def test_container_name_format():
    assert container_name("abc-123") == "codex-agent-abc-123"


# --- spawn_agent ---

def _mock_docker_client():
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("not found")
    return client


def test_spawn_agent_calls_containers_run():
    mock_client = _mock_docker_client()
    with patch("src.master.agent_runner._client", return_value=mock_client):
        spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="codex-slack-master:latest",
            mqtt_host="mosquitto",
            mqtt_port=1883,
            network="codex-slack_internal",
        )
    mock_client.containers.run.assert_called_once()
    _, kwargs = mock_client.containers.run.call_args
    assert kwargs["name"] == "codex-agent-ws1"
    assert kwargs["network"] == "codex-slack_internal"
    assert kwargs["detach"] is True


def test_spawn_agent_sets_environment():
    mock_client = _mock_docker_client()
    with patch("src.master.agent_runner._client", return_value=mock_client):
        spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="img",
            mqtt_host="mosquitto",
            mqtt_port=1883,
            network="net",
        )
    _, kwargs = mock_client.containers.run.call_args
    env = kwargs["environment"]
    assert env["WORKSPACE_ID"] == "ws1"
    assert env["MQTT_HOST"] == "mosquitto"
    assert env["MQTT_PORT"] == "1883"
    assert env["AGENT_REPO_URL"] == "https://github.com/x/y"


def test_spawn_agent_returns_container_name():
    mock_client = _mock_docker_client()
    with patch("src.master.agent_runner._client", return_value=mock_client):
        name = spawn_agent(
            runtime="docker",
            workspace_id="ws-42",
            repo_url="https://github.com/x/y",
            image="myimage:latest",
            mqtt_host="mosquitto",
            mqtt_port=1883,
            network="mynet",
        )
    assert name == "codex-agent-ws-42"


def test_spawn_agent_forwards_oauth_token():
    mock_client = _mock_docker_client()
    with patch("src.master.agent_runner._client", return_value=mock_client):
        spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="img",
            mqtt_host="broker",
            mqtt_port=1883,
            network="net",
            claude_code_oauth_token="mytoken",
        )
    _, kwargs = mock_client.containers.run.call_args
    assert kwargs["environment"]["CLAUDE_CODE_OAUTH_TOKEN"] == "mytoken"


def test_spawn_agent_omits_empty_credentials():
    mock_client = _mock_docker_client()
    with patch("src.master.agent_runner._client", return_value=mock_client):
        spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="img",
            mqtt_host="broker",
            mqtt_port=1883,
            network="net",
            claude_code_oauth_token=None,
            anthropic_api_key=None,
        )
    _, kwargs = mock_client.containers.run.call_args
    env = kwargs["environment"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_spawn_agent_dry_run_skips_docker():
    with patch("src.master.agent_runner._client") as mock_client_fn:
        name = spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="img",
            mqtt_host="broker",
            mqtt_port=1883,
            network="net",
            dry_run=True,
        )
    mock_client_fn.assert_not_called()
    assert name == "codex-agent-ws1"


def test_spawn_agent_removes_existing_container():
    mock_client = MagicMock()
    existing = MagicMock()
    mock_client.containers.get.return_value = existing
    with patch("src.master.agent_runner._client", return_value=mock_client):
        spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="img",
            mqtt_host="broker",
            mqtt_port=1883,
            network="net",
        )
    existing.remove.assert_called_once_with(force=True)


def test_spawn_agent_uses_gh_token_fallback_when_none():
    mock_client = _mock_docker_client()
    with patch("src.master.agent_runner._client", return_value=mock_client):
        spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="img",
            mqtt_host="broker",
            mqtt_port=1883,
            network="net",
            gh_token=None,
        )
    _, kwargs = mock_client.containers.run.call_args
    gh = kwargs["environment"]["GH_TOKEN"]
    assert gh and gh != "None"


# --- stop_agent ---

def test_stop_agent_removes_container():
    mock_client = MagicMock()
    container = MagicMock()
    mock_client.containers.get.return_value = container
    with patch("src.master.agent_runner._client", return_value=mock_client):
        stop_agent(runtime="docker", name="codex-agent-ws1")
    container.remove.assert_called_once_with(force=True)


def test_stop_agent_ignores_not_found():
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
    with patch("src.master.agent_runner._client", return_value=mock_client):
        stop_agent(runtime="docker", name="codex-agent-ws1")


def test_stop_agent_dry_run_skips_call():
    with patch("src.master.agent_runner._client") as mock_client_fn:
        stop_agent(runtime="docker", name="codex-agent-ws1", dry_run=True)
    mock_client_fn.assert_not_called()
