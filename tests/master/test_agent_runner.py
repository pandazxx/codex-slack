from __future__ import annotations

from unittest.mock import MagicMock, patch

import docker.errors
import pytest

import os
import subprocess

from src.master.agent_runner import container_name, resolve_agent_image, spawn_agent, stop_agent


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


def test_spawn_agent_passes_mem_limit_when_set():
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
            mem_limit="1g",
        )
    _, kwargs = mock_client.containers.run.call_args
    assert kwargs["mem_limit"] == "1g"


def test_spawn_agent_omits_mem_limit_when_none():
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
            mem_limit=None,
        )
    _, kwargs = mock_client.containers.run.call_args
    assert "mem_limit" not in kwargs


def test_spawn_agent_omits_mem_limit_when_empty_string():
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
            mem_limit="",
        )
    _, kwargs = mock_client.containers.run.call_args
    assert "mem_limit" not in kwargs


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


# --- resolve_agent_image ---

def _ok_clone(extra_files: dict[str, str] | None = None):
    """Return a subprocess.run replacement that fakes a successful git clone."""
    def fake(args, **kwargs):
        clone_dest = args[-1]
        os.makedirs(clone_dest, exist_ok=True)
        if extra_files:
            for rel_path, content in extra_files.items():
                full = os.path.join(clone_dest, rel_path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w") as f:
                    f.write(content)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
    return fake


def test_resolve_agent_image_dry_run_returns_default_without_cloning():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        result = resolve_agent_image(
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            default_image="base:latest",
            dry_run=True,
        )
    assert result == "base:latest"
    mock_run.assert_not_called()


def test_resolve_agent_image_falls_back_on_clone_failure():
    def fail_clone(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=128, stdout="", stderr="fatal: not found")

    with patch("src.master.agent_runner.subprocess.run", side_effect=fail_clone):
        result = resolve_agent_image(
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            default_image="base:latest",
        )
    assert result == "base:latest"


def test_resolve_agent_image_returns_default_when_no_dockerfile():
    with patch("src.master.agent_runner.subprocess.run", side_effect=_ok_clone()):
        result = resolve_agent_image(
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            default_image="base:latest",
        )
    assert result == "base:latest"


def test_resolve_agent_image_builds_project_image_when_dockerfile_present():
    dockerfile_files = {".prj_assistant/image/Dockerfile": "FROM alpine:3.20\n"}
    mock_docker = MagicMock()

    with patch("src.master.agent_runner.subprocess.run", side_effect=_ok_clone(dockerfile_files)):
        with patch("src.master.agent_runner._client", return_value=mock_docker):
            result = resolve_agent_image(
                workspace_id="ws1",
                repo_url="https://github.com/x/y",
                default_image="base:latest",
            )

    assert result == "codex-agent-ws1:latest"
    mock_docker.images.build.assert_called_once()
    _, build_kwargs = mock_docker.images.build.call_args
    assert build_kwargs["tag"] == "codex-agent-ws1:latest"
    assert build_kwargs["dockerfile"] == "Dockerfile"
    assert build_kwargs["pull"] is True


def test_resolve_agent_image_injects_gh_token_into_github_url():
    seen_url: list[str] = []

    def capture_clone(args, **kwargs):
        seen_url.append(args[6])  # position of clone_url in: git clone --depth 1 --branch <ref> <url> <dest>
        clone_dest = args[-1]
        os.makedirs(clone_dest, exist_ok=True)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("src.master.agent_runner.subprocess.run", side_effect=capture_clone):
        resolve_agent_image(
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            default_image="base:latest",
            gh_token="secret-token",
        )

    assert len(seen_url) == 1
    assert "x-access-token:secret-token@github.com" in seen_url[0]


def test_resolve_agent_image_cleans_up_temp_dir_on_success():
    created_dirs: list[str] = []
    original_mkdtemp = __import__("tempfile").mkdtemp

    def capturing_mkdtemp(**kwargs):
        d = original_mkdtemp(**kwargs)
        created_dirs.append(d)
        return d

    dockerfile_files = {".prj_assistant/image/Dockerfile": "FROM alpine:3.20\n"}
    mock_docker = MagicMock()

    with patch("src.master.agent_runner.tempfile.mkdtemp", side_effect=capturing_mkdtemp):
        with patch("src.master.agent_runner.subprocess.run", side_effect=_ok_clone(dockerfile_files)):
            with patch("src.master.agent_runner._client", return_value=mock_docker):
                resolve_agent_image(
                    workspace_id="ws1",
                    repo_url="https://github.com/x/y",
                    default_image="base:latest",
                )

    assert created_dirs
    for d in created_dirs:
        assert not os.path.exists(d), f"temp dir {d} was not cleaned up"
