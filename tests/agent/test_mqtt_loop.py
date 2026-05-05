from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from src.agent.mqtt_loop import (
    _fetch_attachment,
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


# ---------------------------------------------------------------------------
# Helpers for Popen-based mocking
# ---------------------------------------------------------------------------

def _make_popen_mock(json_events: list[dict], stderr: str = "", returncode: int = 0) -> MagicMock:
    """Return a mock that behaves like a subprocess.Popen process.

    proc.stdout iterates over JSON-encoded event lines.
    proc.stderr.read() returns the given stderr string.
    proc.wait() is a no-op.
    """
    lines = [json.dumps(e) + "\n" for e in json_events]
    proc = MagicMock()
    proc.stdout = iter(lines)
    proc.stderr.read.return_value = stderr
    proc.wait.return_value = returncode
    return proc


# --- _run_claude ---

def test_run_claude_returns_stdout(tmp_path):
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello from claude"}]}},
        {"type": "result", "result": "Hello from claude", "session_id": None, "cost_usd": 0.0, "duration_ms": 100, "is_error": False},
    ]
    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)):
        text, session, transcript = _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "say hi", None, False, None, None, None)
    assert text == "Hello from claude"
    assert session is None
    assert transcript is not None
    assert isinstance(json.loads(transcript), list)


def test_run_claude_joins_multiple_final_responses(tmp_path):
    """When the CLI emits more than one result event (e.g. main response + background
    task notification), all result texts must be joined so nothing is dropped."""
    import json as _json
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "First response"}]}},
        {"type": "result", "result": "First response", "session_id": "s1", "is_error": False},
        {"type": "system", "subtype": "task_updated"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Second response"}]}},
        {"type": "result", "result": "Second response", "session_id": "s1", "is_error": False},
    ]
    stream = "\n".join(_json.dumps(e) for e in events)
    with patch("src.agent.mqtt_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=stream, stderr="", returncode=0)
        text, session, transcript = _run_claude(str(tmp_path), "run it", None, False, None, None, None)
    assert "First response" in text
    assert "Second response" in text
    assert session == "s1"


def test_run_claude_includes_resume_when_session_id(tmp_path):
    events = [{"type": "result", "result": "ok", "session_id": "ses-123", "is_error": False}]
    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
        _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "continue", "ses-123", False, None, None, None)
    cmd = mock_popen.call_args.args[0]
    assert "--resume" in cmd
    assert "ses-123" in cmd


def test_run_claude_uses_session_id_flag_when_new(tmp_path):
    events = [{"type": "result", "result": "ok", "session_id": "ses-new", "is_error": False}]
    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
        _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "start", "ses-new", True, None, None, None)
    cmd = mock_popen.call_args.args[0]
    assert "--session-id" in cmd
    assert "--resume" not in cmd


def test_run_claude_passes_model_and_system_prompt(tmp_path):
    events = [{"type": "result", "result": "ok", "session_id": None, "is_error": False}]
    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
        _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "hi", None, True, None, "claude-opus-4-7", "You are a reviewer")
    cmd = mock_popen.call_args.args[0]
    assert "--model" in cmd and "claude-opus-4-7" in cmd
    assert "--append-system-prompt" in cmd and "You are a reviewer" in cmd


def test_run_claude_retries_fresh_on_session_not_found(tmp_path):
    error_events = [{"type": "result", "is_error": True, "result": "No conversation found with session ID: old-sid", "session_id": "dead-sid"}]
    ok_events = [{"type": "result", "is_error": False, "result": "Hello fresh", "session_id": "new-sid"}]
    call_count = {"n": 0}
    cmds = []

    def popen_side_effect(cmd, **kwargs):
        call_count["n"] += 1
        cmds.append(cmd)
        if call_count["n"] == 1:
            return _make_popen_mock(error_events, returncode=1)
        return _make_popen_mock(ok_events, returncode=0)

    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=popen_side_effect):
        text, sid, transcript = _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "hi", "old-sid", False, None, None, None)

    assert call_count["n"] == 2
    assert text == "Hello fresh"
    assert sid == "new-sid"
    # Retry uses --session-id (starts fresh under same UUID)
    assert "--session-id" in cmds[1]


def test_run_claude_does_not_retry_without_session(tmp_path):
    error_events = [{"type": "result", "is_error": True, "result": "some other error", "session_id": None}]
    call_count = {"n": 0}

    def popen_side_effect(cmd, **kwargs):
        call_count["n"] += 1
        return _make_popen_mock(error_events, returncode=1)

    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=popen_side_effect):
        _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "hi", None, False, None, None, None)

    assert call_count["n"] == 1


def test_run_claude_not_found(tmp_path):
    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=FileNotFoundError()):
        text, _, _t = _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "hi", None, False, None, None, None)
    assert "not found" in text


def test_run_claude_timeout(tmp_path):
    import subprocess as sp
    client = MagicMock()

    # Simulate a timeout by making proc.stdout iteration raise TimeoutExpired
    proc = MagicMock()
    proc.stdout = iter([])  # empty — triggers proc.wait()
    proc.stderr.read.return_value = "timed out"
    proc.wait.side_effect = sp.TimeoutExpired("claude", 300)

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        text, _, _t = _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "hi", None, False, None, None, None)
    assert "timed out" in text or "claude error" in text


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
    with patch("src.agent.mqtt_loop._run_claude", return_value=("the answer", None, '[{"type":"result","result":"the answer"}]')):
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


# --- _fetch_attachment ---

def test_fetch_attachment_writes_file(tmp_path):
    att_id = "att-abc"
    filename = "report.txt"
    content = b"attachment bytes"

    class FakeResp:
        def read(self):
            return content
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with patch("src.agent.mqtt_loop.urllib.request.urlopen", return_value=FakeResp()):
        _fetch_attachment("http://master:8080", att_id, filename, str(tmp_path))

    dest = tmp_path / filename
    assert dest.exists()
    assert dest.read_bytes() == content


def test_fetch_attachment_constructs_correct_url(tmp_path):
    att_id = "att-xyz"
    filename = "image.png"

    class FakeResp:
        def read(self):
            return b"img"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with patch("src.agent.mqtt_loop.urllib.request.urlopen", return_value=FakeResp()) as mock_urlopen:
        _fetch_attachment("http://master:8080", att_id, filename, str(tmp_path))

    called_url = mock_urlopen.call_args.args[0]
    assert called_url == f"http://master:8080/api/attachments/{att_id}/download"


# --- _process_prompt with attachments ---

def test_process_prompt_with_attachments_fetches_files(tmp_path):
    client = MagicMock()
    with patch("src.agent.mqtt_loop._run_claude", return_value=("response", None, None)):
        with patch("src.agent.mqtt_loop._ensure_worktree"):
            with patch("src.agent.mqtt_loop._fetch_attachment") as mock_fetch:
                _process_prompt(
                    client, "ws1", "t1",
                    {
                        "message_id": "m1",
                        "text": "analyze the file",
                        "adapter": "claude-code",
                        "worktree": str(tmp_path),
                        "branch": "feat/t",
                        "session_id": None,
                        "attachments": [{"id": "att-1", "filename": "data.csv", "mime_type": "text/csv"}],
                        "master_url": "http://master:8080",
                    },
                    repo_dir=str(tmp_path),
                )
    mock_fetch.assert_called_once_with("http://master:8080", "att-1", "data.csv", str(tmp_path))


def test_process_prompt_with_attachments_prepends_note(tmp_path):
    client = MagicMock()
    captured_text = {}

    def capture_run_claude(client, workspace_id, topic_id, reply_message_id, agent_name,
                           worktree, text, session_id, is_new_session, subagent, model, system_prompt):
        captured_text["text"] = text
        return ("response", None, None)

    with patch("src.agent.mqtt_loop._run_claude", side_effect=capture_run_claude):
        with patch("src.agent.mqtt_loop._ensure_worktree"):
            with patch("src.agent.mqtt_loop._fetch_attachment"):
                _process_prompt(
                    client, "ws1", "t1",
                    {
                        "message_id": "m1",
                        "text": "check it out",
                        "adapter": "claude-code",
                        "worktree": str(tmp_path),
                        "branch": "feat/t",
                        "session_id": None,
                        "attachments": [{"id": "att-1", "filename": "notes.txt", "mime_type": "text/plain"}],
                        "master_url": "http://master:8080",
                    },
                    repo_dir=str(tmp_path),
                )

    assert "Attached file: notes.txt" in captured_text["text"]
    assert "available in the current directory" in captured_text["text"]
    assert "check it out" in captured_text["text"]


def test_process_prompt_no_attachments_no_note(tmp_path):
    client = MagicMock()
    captured_text = {}

    def capture_run_claude(client, workspace_id, topic_id, reply_message_id, agent_name,
                           worktree, text, session_id, is_new_session, subagent, model, system_prompt):
        captured_text["text"] = text
        return ("response", None, None)

    with patch("src.agent.mqtt_loop._run_claude", side_effect=capture_run_claude):
        with patch("src.agent.mqtt_loop._ensure_worktree"):
            _process_prompt(
                client, "ws1", "t1",
                {
                    "message_id": "m1",
                    "text": "plain prompt",
                    "adapter": "claude-code",
                    "worktree": str(tmp_path),
                    "branch": "feat/t",
                    "session_id": None,
                    "attachments": [],
                },
                repo_dir=str(tmp_path),
            )

    assert captured_text["text"] == "plain prompt"
