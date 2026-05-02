from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from src.master.agent_runner import container_name, spawn_agent, stop_agent


def test_container_name_format():
    assert container_name("abc-123") == "codex-agent-abc-123"


# --- spawn_agent ---

def test_spawn_agent_runs_docker_run():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        spawn_agent(
            runtime="docker",
            workspace_id="ws1",
            repo_url="https://github.com/x/y",
            image="codex-slack-master:latest",
            mqtt_host="mosquitto",
            mqtt_port=1883,
            network="codex-slack_internal",
        )
    calls = [c.args[0] for c in mock_run.call_args_list]
    run_cmd = next(c for c in calls if "run" in c)
    assert "docker" in run_cmd
    assert "run" in run_cmd
    assert "-d" in run_cmd
    assert "--network" in run_cmd
    assert "codex-slack_internal" in run_cmd
    assert any("WORKSPACE_ID=ws1" in a for a in run_cmd)
    assert any("MQTT_HOST=mosquitto" in a for a in run_cmd)
    assert any("AGENT_REPO_URL=https://github.com/x/y" in a for a in run_cmd)


def test_spawn_agent_returns_container_name():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
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
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
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
    calls = [c.args[0] for c in mock_run.call_args_list]
    run_cmd = next(c for c in calls if "run" in c)
    assert any("CLAUDE_CODE_OAUTH_TOKEN=mytoken" in a for a in run_cmd)


def test_spawn_agent_omits_empty_credentials():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
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
    calls = [c.args[0] for c in mock_run.call_args_list]
    run_cmd = next(c for c in calls if "run" in c)
    assert not any("CLAUDE_CODE_OAUTH_TOKEN" in a for a in run_cmd)
    assert not any("ANTHROPIC_API_KEY" in a for a in run_cmd)


def test_spawn_agent_dry_run_skips_docker():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
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
    # Only the rm -f cleanup call should happen, not the run
    calls = [c.args[0] for c in mock_run.call_args_list]
    assert not any("run" in c for c in calls)
    assert name == "codex-agent-ws1"


def test_spawn_agent_raises_on_docker_failure():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        # First call (rm -f) succeeds, second (run) fails
        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=1, stderr="image not found"),
        ]
        with pytest.raises(RuntimeError, match="image not found"):
            spawn_agent(
                runtime="docker",
                workspace_id="ws1",
                repo_url="https://github.com/x/y",
                image="bad-image",
                mqtt_host="broker",
                mqtt_port=1883,
                network="net",
            )


def test_spawn_agent_uses_gh_token_fallback_when_none():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
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
    calls = [c.args[0] for c in mock_run.call_args_list]
    run_cmd = next(c for c in calls if "run" in c)
    assert any("GH_TOKEN=" in a for a in run_cmd)
    assert not any("GH_TOKEN=None" in a for a in run_cmd)


# --- stop_agent ---

def test_stop_agent_runs_rm_f():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        stop_agent(runtime="docker", name="codex-agent-ws1")
    cmd = mock_run.call_args.args[0]
    assert cmd == ["docker", "rm", "-f", "codex-agent-ws1"]


def test_stop_agent_dry_run_skips_call():
    with patch("src.master.agent_runner.subprocess.run") as mock_run:
        stop_agent(runtime="docker", name="codex-agent-ws1", dry_run=True)
    mock_run.assert_not_called()
