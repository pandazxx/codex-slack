from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from src.agent.mqtt_loop import (
    _parse_prompt_topic,
    _process_prompt,
    _run_claude,
    _run_codex,
    _ensure_worktree,
    _on_connect,
    _on_message,
)


# --- _parse_prompt_topic ---

def test_parse_prompt_topic_valid():
    raw = "codex-slack/workspace/ws1/topic/t1/prompt"
    assert _parse_prompt_topic(raw) == ("ws1", "t1")


def test_parse_prompt_topic_rejects_response():
    raw = "codex-slack/workspace/ws1/topic/t1/response"
    assert _parse_prompt_topic(raw) is None


def test_parse_prompt_topic_rejects_malformed():
    assert _parse_prompt_topic("too/short") is None
    assert _parse_prompt_topic("a/b/c/d/e/f/g") is None


# --- _run_claude ---

def test_run_claude_returns_stdout(tmp_path):
    with patch("src.agent.mqtt_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="Hello from claude\n", stderr="", returncode=0)
        text, session, transcript = _run_claude(str(tmp_path), "say hi", None, None)
    assert text == "Hello from claude"
    assert session is None


def test_run_claude_includes_resume_when_session_id(tmp_path):
    with patch("src.agent.mqtt_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        _run_claude(str(tmp_path), "continue", "ses-123", None)
    cmd = mock_run.call_args.args[0]
    assert "--resume" in cmd
    assert "ses-123" in cmd


def test_run_claude_not_found(tmp_path):
    import subprocess as sp
    with patch("src.agent.mqtt_loop.subprocess.run", side_effect=FileNotFoundError()):
        text, _, _t = _run_claude(str(tmp_path), "hi", None, None)
    assert "not found" in text


def test_run_claude_timeout(tmp_path):
    import subprocess as sp
    with patch("src.agent.mqtt_loop.subprocess.run", side_effect=sp.TimeoutExpired("claude", 300)):
        text, _, _t = _run_claude(str(tmp_path), "hi", None, None)
    assert "timed out" in text


# --- _run_codex ---

def test_run_codex_returns_stdout(tmp_path):
    with patch("src.agent.mqtt_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="codex output\n", stderr="", returncode=0)
        text, session, transcript = _run_codex(str(tmp_path), "do it")
    assert text == "codex output"
    assert session is None
    assert transcript is None


def test_run_codex_not_found(tmp_path):
    with patch("src.agent.mqtt_loop.subprocess.run", side_effect=FileNotFoundError()):
        text, _, _t = _run_codex(str(tmp_path), "hi")
    assert "not found" in text


# --- _ensure_worktree ---

def test_ensure_worktree_skips_when_exists(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with patch("src.agent.mqtt_loop.subprocess.run") as mock_run:
        _ensure_worktree(str(tmp_path / "repo"), str(wt), "feat/test")
    mock_run.assert_not_called()


def test_ensure_worktree_creates_with_new_branch(tmp_path):
    wt = tmp_path / "wt"
    with patch("src.agent.mqtt_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _ensure_worktree(str(tmp_path / "repo"), str(wt), "feat/new")
    args = mock_run.call_args.args[0]
    assert "worktree" in args
    assert "add" in args
    assert "-b" in args


def test_ensure_worktree_falls_back_on_existing_branch(tmp_path):
    import subprocess as sp
    wt = tmp_path / "wt"
    calls = []

    def side_effect(cmd, **kwargs):
        calls.append(cmd)
        if "-b" in cmd:
            raise sp.CalledProcessError(128, cmd)
        return MagicMock(returncode=0)

    with patch("src.agent.mqtt_loop.subprocess.run", side_effect=side_effect):
        _ensure_worktree(str(tmp_path / "repo"), str(wt), "feat/existing")

    assert len(calls) == 2
    assert "-b" in calls[0]
    assert "-b" not in calls[1]


# --- _process_prompt ---

def test_process_prompt_publishes_thinking_then_response(tmp_path):
    client = MagicMock()
    with patch("src.agent.mqtt_loop._run_claude", return_value=("response text", None, None)):
        with patch("src.agent.mqtt_loop._ensure_worktree"):
            _process_prompt(
                client, "ws1", "t1",
                {"message_id": "m1", "text": "hello", "adapter": "claude-code",
                 "worktree": str(tmp_path), "branch": "feat/t", "session_id": None},
                repo_dir=str(tmp_path),
            )
    published_topics = [c.args[0] for c in client.publish.call_args_list]
    assert any("status" in t for t in published_topics)
    assert any("response" in t for t in published_topics)


def test_process_prompt_uses_codex_adapter(tmp_path):
    client = MagicMock()
    with patch("src.agent.mqtt_loop._run_codex", return_value=("codex out", None, None)) as mock_codex:
        with patch("src.agent.mqtt_loop._ensure_worktree"):
            _process_prompt(
                client, "ws1", "t1",
                {"message_id": "m1", "text": "run it", "adapter": "codex",
                 "worktree": str(tmp_path), "branch": "feat/t", "session_id": None},
                repo_dir=str(tmp_path),
            )
    mock_codex.assert_called_once()


def test_process_prompt_skips_empty_text(tmp_path):
    client = MagicMock()
    _process_prompt(
        client, "ws1", "t1",
        {"message_id": "m1", "text": "", "adapter": "claude-code",
         "worktree": str(tmp_path), "branch": "feat/t", "session_id": None},
        repo_dir=str(tmp_path),
    )
    client.publish.assert_not_called()


def test_process_prompt_response_payload_fields(tmp_path):
    client = MagicMock()
    with patch("src.agent.mqtt_loop._run_claude", return_value=("the answer", None, '{"result":"the answer"}')):
        with patch("src.agent.mqtt_loop._ensure_worktree"):
            _process_prompt(
                client, "ws1", "t1",
                {"message_id": "orig-id", "text": "q", "adapter": "claude-code",
                 "worktree": str(tmp_path), "branch": "feat/t", "session_id": None},
                repo_dir=str(tmp_path),
            )
    # Find the response publish call
    response_call = next(
        c for c in client.publish.call_args_list
        if "response" in c.args[0]
    )
    payload = json.loads(response_call.args[1])
    assert payload["reply_to"] == "orig-id"
    assert payload["last_response"] == "the answer"
    assert "message_id" in payload
    assert "transcript" in payload


# --- _on_message dispatch ---

def test_on_message_submits_to_executor():
    client = MagicMock()
    userdata = {"workspace_id": "ws1", "repo_dir": "/repo"}
    msg = MagicMock()
    msg.topic = "codex-slack/workspace/ws1/topic/t1/prompt"
    msg.payload = json.dumps({"text": "hi", "adapter": "claude-code"}).encode()

    with patch("src.agent.mqtt_loop._executor") as mock_exec:
        _on_message(client, userdata, msg)
    mock_exec.submit.assert_called_once()


def test_on_message_ignores_malformed_json():
    client = MagicMock()
    userdata = {"workspace_id": "ws1", "repo_dir": ""}
    msg = MagicMock()
    msg.topic = "codex-slack/workspace/ws1/topic/t1/prompt"
    msg.payload = b"not-json"
    with patch("src.agent.mqtt_loop._executor") as mock_exec:
        _on_message(client, userdata, msg)
    mock_exec.submit.assert_not_called()


# --- _on_connect ---

def test_on_connect_subscribes_to_workspace_topic():
    client = MagicMock()
    reason = MagicMock()
    reason.is_failure = False
    _on_connect(client, {"workspace_id": "ws42"}, None, reason, None)
    topic = client.subscribe.call_args.args[0]
    assert "ws42" in topic
    assert topic.endswith("/prompt")
