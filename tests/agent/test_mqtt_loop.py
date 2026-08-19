from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

import src.agent.mqtt_loop as mqtt_loop_module
from src.agent.mqtt_loop import (
    _fetch_attachment,
    _kill_proc_tree,
    _parse_prompt_topic,
    _process_prompt,
    _publish_interrupted_all,
    _run_claude,
    _run_codex,
    _ensure_worktree,
    _on_connect,
    _on_message,
)


# --- _kill_proc_tree defensive guard ---

def test_kill_proc_tree_refuses_pgid_1():
    """pgid=1 (init/tini) -> os.killpg issues kill(-1, sig) which broadcasts.
    Guard at src/agent/mqtt_loop.py must refuse this."""
    proc = MagicMock()
    proc.pid = 1
    with patch("src.agent.mqtt_loop.os.killpg") as mock_killpg:
        _kill_proc_tree(proc)
    mock_killpg.assert_not_called()


def test_kill_proc_tree_refuses_pgid_0():
    """pgid=0 targets the caller's own process group; also self-destruct."""
    proc = MagicMock()
    proc.pid = 0
    with patch("src.agent.mqtt_loop.os.killpg") as mock_killpg:
        _kill_proc_tree(proc)
    mock_killpg.assert_not_called()


def test_kill_proc_tree_refuses_magicmock_pid():
    """A bare MagicMock proc has __int__() = 1 by default — this is the exact
    bug pattern that took down prod on 2026-05-18. The guard must catch it."""
    proc = MagicMock()  # proc.pid is a MagicMock, int() returns 1
    with patch("src.agent.mqtt_loop.os.killpg") as mock_killpg:
        _kill_proc_tree(proc)
    mock_killpg.assert_not_called()


def test_kill_proc_tree_kills_legitimate_pgid():
    """A normal subprocess pgid (>= 2) must still be killed."""
    proc = MagicMock()
    proc.pid = 12345
    import signal
    with patch("src.agent.mqtt_loop.os.killpg") as mock_killpg:
        _kill_proc_tree(proc)
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)


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
    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)):
        text, session, transcript = _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "run it", None, False, None, None, None)
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
    # Belt: explicit safe pid. MagicMock().__int__() defaults to 1, which would
    # cause os.killpg(proc.pid, SIGKILL) to issue kill(-1, SIGKILL) and broadcast
    # SIGKILL across the whole container — 2026-05-18 prod incident root cause.
    proc.pid = 999999
    proc.stdout = iter([])  # empty — triggers proc.wait()
    proc.stderr.read.return_value = "timed out"
    proc.wait.side_effect = sp.TimeoutExpired("claude", 300)

    # Suspenders: mock os.killpg so even if the safe pid were missed, no real
    # syscall fires.
    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc), \
         patch("src.agent.mqtt_loop.os.killpg"):
        text, _, _t = _run_claude(client, "ws1", "t1", "reply-id", "claude", str(tmp_path), "hi", None, False, None, None, None)
    assert "timed out" in text or "claude error" in text


# --- _run_codex ---

def test_run_codex_returns_stdout(tmp_path):
    events = [
        {"type": "thread.started", "thread_id": "abc"},
        {"type": "turn.started"},
        {"type": "turn.completed", "output_text": "codex output", "last_message": "codex output"},
    ]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("codex output")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)):
                text, session, transcript = _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "do it", None)
    assert text == "codex output"
    assert session == "abc"  # thread_id captured from thread.started even on first turn
    assert transcript is not None


def test_run_codex_streams_chunks(tmp_path):
    events = [
        {"type": "turn.started"},
        {"type": "turn.completed", "output_text": "finished"},
    ]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("finished")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)):
                _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "do it", None)
    chunk_calls = [c for c in client.publish.call_args_list if "chunk" in c.args[0]]
    assert len(chunk_calls) == 2


def test_run_codex_falls_back_to_stderr(tmp_path):
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("")  # empty — no output from codex
    proc = _make_popen_mock([], stderr="something went wrong")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
                text, _, _ = _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", None)
    assert "something went wrong" in text


def test_run_codex_not_found(tmp_path):
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=FileNotFoundError()):
                text, _, _t = _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", None)
    assert "not found" in text


def test_run_codex_passes_model(tmp_path):
    events = [{"type": "turn.completed", "output_text": "ok"}]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("ok")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
                _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", "o4-mini")
    cmd = mock_popen.call_args.args[0]
    assert "-m" in cmd and "o4-mini" in cmd


def test_run_codex_correct_command_flags(tmp_path):
    events = [{"type": "turn.completed", "output_text": "ok"}]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("ok")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
                _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", None)
    cmd = mock_popen.call_args.args[0]
    assert cmd[0] == "codex"
    assert cmd[1] == "exec"
    assert "--json" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "-s" in cmd and "danger-full-access" in cmd
    # default scope is "topic" → persistent session, no --ephemeral
    assert "--ephemeral" not in cmd


def test_run_codex_first_turn_captures_session_id(tmp_path):
    """First turn has no session_id; codex should run without --ephemeral and
    the thread_id from thread.started must be returned so master can persist it."""
    events = [
        {"type": "thread.started", "thread_id": "first-turn-sid"},
        {"type": "turn.completed", "output_text": "hello"},
    ]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("hello")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
                text, session, transcript = _run_codex(
                    client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", None,
                    session_id=None, is_new_session=True, session_scope="topic",
                )
    cmd = mock_popen.call_args.args[0]
    assert "--ephemeral" not in cmd
    assert "resume" not in cmd
    assert session == "first-turn-sid"
    assert text == "hello"


def test_run_codex_ephemeral_when_scope_none(tmp_path):
    events = [{"type": "turn.completed", "output_text": "ok"}]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("ok")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
                _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", None,
                           session_id="some-uuid", is_new_session=True, session_scope="none")
    cmd = mock_popen.call_args.args[0]
    assert "--ephemeral" in cmd
    assert "resume" not in cmd


def test_run_codex_new_session_no_ephemeral(tmp_path):
    events = [
        {"type": "thread.started", "thread_id": "codex-sess-1"},
        {"type": "turn.completed", "output_text": "ok"},
    ]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("ok")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
                text, session, transcript = _run_codex(
                    client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", None,
                    session_id="my-uuid", is_new_session=True, session_scope="topic",
                )
    cmd = mock_popen.call_args.args[0]
    assert "--ephemeral" not in cmd
    assert "resume" not in cmd
    assert session == "codex-sess-1"


def test_run_codex_resumes_session(tmp_path):
    events = [
        {"type": "thread.started", "thread_id": "codex-sess-1"},
        {"type": "turn.completed", "output_text": "continued"},
    ]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("continued")
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=_make_popen_mock(events)) as mock_popen:
                text, session, transcript = _run_codex(
                    client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "follow up", None,
                    session_id="codex-sess-1", is_new_session=False, session_scope="topic",
                )
    cmd = mock_popen.call_args.args[0]
    assert "--ephemeral" not in cmd
    assert "resume" in cmd
    assert "codex-sess-1" in cmd
    assert "-" in cmd  # stdin indicator
    assert text == "continued"


def test_run_codex_retries_as_new_on_session_not_found(tmp_path):
    """When resume fails with 'no rollout found', _run_codex retries as a new session."""
    error_output = "no rollout found for thread id abc-123"
    retry_output = "hello from new session"
    client = MagicMock()

    call_count = 0

    def fake_stream_codex_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return error_output, None, None, True
        return retry_output, "new-sid", json.dumps([]), False

    with patch("src.agent.mqtt_loop._stream_codex_once", side_effect=fake_stream_codex_once):
        text, sid, transcript = _run_codex(
            client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hello", None,
            session_id="old-sid", is_new_session=False, session_scope="topic",
        )

    assert call_count == 2
    assert text == retry_output
    assert sid == "new-sid"
    published = [json.loads(c.args[1]) for c in client.publish.call_args_list]
    assert any(p.get("event", {}).get("subtype") == "retry" for p in published)


def test_run_codex_turn_failed_is_error(tmp_path):
    events = [{"type": "turn.failed", "error": {"message": "auth failed"}}]
    client = MagicMock()
    output_file = tmp_path / "out.txt"
    output_file.write_text("")
    proc = _make_popen_mock(events)
    proc.pid = 999999  # see test_run_claude_timeout for rationale
    proc.returncode = 1
    with patch("src.agent.mqtt_loop.tempfile.mkstemp", return_value=(0, str(output_file))):
        with patch("src.agent.mqtt_loop.os.close"):
            with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc), \
                 patch("src.agent.mqtt_loop.os.killpg"):
                text, _, _ = _run_codex(client, "ws1", "t1", "reply-id", "codex", str(tmp_path), "hi", None)
    assert "auth failed" in text


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

    # Only the worktree-add attempts matter here (a leading fetch call is expected too).
    worktree_calls = [c for c in calls if "worktree" in c]
    assert len(worktree_calls) == 2
    assert "-b" in worktree_calls[0]
    assert "-b" not in worktree_calls[1]


def test_ensure_worktree_fetches_before_branching(tmp_path):
    """Regression for #250: the remote is fetched before the worktree is cut, so the
    topic branch is not created off a stale origin/<repo_ref>."""
    wt = tmp_path / "wt"
    calls = []

    def side_effect(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    with patch("src.agent.mqtt_loop.subprocess.run", side_effect=side_effect):
        _ensure_worktree(str(tmp_path / "repo"), str(wt), "feat/new", repo_ref="main")

    # A fetch of origin/main must happen, and it must precede the worktree add.
    fetch_idx = next(i for i, c in enumerate(calls) if "fetch" in c)
    worktree_idx = next(i for i, c in enumerate(calls) if "worktree" in c)
    assert fetch_idx < worktree_idx
    assert calls[fetch_idx][-2:] == ["origin", "main"]


def test_ensure_worktree_survives_fetch_failure(tmp_path):
    """A failed fetch (offline/transient) must not block worktree creation."""
    wt = tmp_path / "wt"
    calls = []

    def side_effect(cmd, **kwargs):
        calls.append(cmd)
        if "fetch" in cmd:
            return MagicMock(returncode=1, stderr="could not resolve host")
        return MagicMock(returncode=0)

    with patch("src.agent.mqtt_loop.subprocess.run", side_effect=side_effect):
        _ensure_worktree(str(tmp_path / "repo"), str(wt), "feat/new", repo_ref="main")

    # Worktree add still ran despite the fetch failing.
    assert any("worktree" in c for c in calls)


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
    subscribed_topics = [call.args[0] for call in client.subscribe.call_args_list]
    assert any("ws42" in t and t.endswith("/prompt") for t in subscribed_topics)
    assert any("ws42" in t and t.endswith("/cancel") for t in subscribed_topics)


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


# ---------------------------------------------------------------------------
# Ping / pong tests
# ---------------------------------------------------------------------------

def _make_ping_msg(workspace_id: str, topic_id: str, message_id: str) -> MagicMock:
    msg = MagicMock()
    msg.topic = f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/ping"
    msg.payload = json.dumps({"message_id": message_id}).encode()
    return msg


def test_pong_alive_true_when_proc_running():
    """Ping for a message whose process is actively running → pong with alive=True."""
    client = MagicMock()
    userdata = {"workspace_id": "ws1", "repo_dir": ""}

    proc = MagicMock()
    proc.poll.return_value = None  # process still running

    with patch.dict(mqtt_loop_module._active_procs, {"msg-run": proc}, clear=True):
        with patch.dict(mqtt_loop_module._active_contexts, {}, clear=True):
            msg = _make_ping_msg("ws1", "t1", "msg-run")
            _on_message(client, userdata, msg)

    publish_calls = client.publish.call_args_list
    pong_call = next((c for c in publish_calls if "pong" in c.args[0]), None)
    assert pong_call is not None, "no pong published"
    payload = json.loads(pong_call.args[1])
    assert payload["message_id"] == "msg-run"
    assert payload["alive"] is True


def test_pong_alive_false_when_proc_not_found():
    """Ping for an unknown message_id → pong with alive=False."""
    client = MagicMock()
    userdata = {"workspace_id": "ws1", "repo_dir": ""}

    with patch.dict(mqtt_loop_module._active_procs, {}, clear=True):
        with patch.dict(mqtt_loop_module._active_contexts, {}, clear=True):
            msg = _make_ping_msg("ws1", "t1", "msg-unknown")
            _on_message(client, userdata, msg)

    pong_call = next((c for c in client.publish.call_args_list if "pong" in c.args[0]), None)
    assert pong_call is not None
    payload = json.loads(pong_call.args[1])
    assert payload["alive"] is False


def test_pong_alive_true_when_proc_exited_but_still_registered():
    """Ping for a message whose process exited but is still in _active_procs (response
    not yet published) → pong with alive=True to prevent premature stream interruption."""
    client = MagicMock()
    userdata = {"workspace_id": "ws1", "repo_dir": ""}

    proc = MagicMock()
    proc.poll.return_value = 0  # process exited with code 0

    with patch.dict(mqtt_loop_module._active_procs, {"msg-done": proc}, clear=True):
        with patch.dict(mqtt_loop_module._active_contexts, {}, clear=True):
            msg = _make_ping_msg("ws1", "t1", "msg-done")
            _on_message(client, userdata, msg)

    pong_call = next((c for c in client.publish.call_args_list if "pong" in c.args[0]), None)
    assert pong_call is not None
    payload = json.loads(pong_call.args[1])
    # Proc exited but still registered: response publish is in flight.
    # alive=True prevents the stale-stream detector from inserting "(message interrupted)".
    assert payload["alive"] is True


def test_publish_interrupted_all():
    """_publish_interrupted_all kills procs and publishes interrupted response for each."""
    client = MagicMock()

    proc = MagicMock()
    proc.pid = 999999  # bare MagicMock().pid resolves to int 1 -> _kill_proc_tree guard refuses
    procs = {"msg-abc": proc}
    contexts = {
        "msg-abc": {
            "workspace_id": "ws1",
            "topic_id": "t1",
            "agent_name": "claude",
        }
    }

    with patch.dict(mqtt_loop_module._active_procs, procs, clear=True):
        with patch.dict(mqtt_loop_module._active_contexts, contexts, clear=True):
            with patch("src.agent.mqtt_loop.os.killpg") as mock_killpg:
                _publish_interrupted_all(client)

    import signal
    mock_killpg.assert_called_once_with(999999, signal.SIGKILL)

    response_calls = [c for c in client.publish.call_args_list if "response" in c.args[0]]
    assert len(response_calls) == 1
    payload = json.loads(response_calls[0].args[1])
    assert payload["message_id"] == "msg-abc"
    assert payload["last_response"] == "(message interrupted)"
    assert payload["agent_name"] == "claude"


# ---------------------------------------------------------------------------
# SIGKILL-survivor path: persist + replay inherited interrupts on startup.
# ---------------------------------------------------------------------------


def test_persist_active_procs_writes_atomic_snapshot(tmp_path):
    """_persist_active_procs_locked dumps _active_contexts to disk atomically."""
    state_path = tmp_path / "active_procs.json"
    contexts = {
        "msg-1": {"workspace_id": "ws1", "topic_id": "t1", "agent_name": "codex"},
        "msg-2": {"workspace_id": "ws1", "topic_id": "t2", "agent_name": "claude"},
    }
    with patch.object(mqtt_loop_module, "_STATE_DIR", tmp_path), \
         patch.object(mqtt_loop_module, "_ACTIVE_PROCS_PATH", state_path), \
         patch.dict(mqtt_loop_module._active_contexts, contexts, clear=True):
        mqtt_loop_module._persist_active_procs_locked()
    assert state_path.exists()
    written = json.loads(state_path.read_text())
    assert written == contexts


def test_publish_inherited_interrupts_replays_each_leftover(tmp_path):
    """On startup, a non-empty active_procs.json triggers `(message interrupted)`
    publishes with interrupt_reason=agent-killed for every leftover entry, and
    the file is then deleted so subsequent reconnects don't re-publish."""
    state_path = tmp_path / "active_procs.json"
    state_path.write_text(json.dumps({
        "msg-killed-1": {"workspace_id": "wsX", "topic_id": "tA", "agent_name": "codex"},
        "msg-killed-2": {"workspace_id": "wsX", "topic_id": "tB", "agent_name": "claude"},
    }))
    client = MagicMock()
    with patch.object(mqtt_loop_module, "_ACTIVE_PROCS_PATH", state_path), \
         patch.object(mqtt_loop_module, "_inherited_already_published", False):
        mqtt_loop_module._publish_inherited_interrupts(client)
    response_calls = [c for c in client.publish.call_args_list if "response" in c.args[0]]
    assert len(response_calls) == 2
    payloads = [json.loads(c.args[1]) for c in response_calls]
    ids = {p["message_id"] for p in payloads}
    assert ids == {"msg-killed-1", "msg-killed-2"}
    for p in payloads:
        assert p["last_response"] == "(message interrupted)"
        assert p["interrupt_reason"] == "agent-killed"
        assert p["transcript"] is None
    # File must be cleared so future _on_connect calls (reconnects) don't replay.
    assert not state_path.exists()


def test_publish_inherited_interrupts_is_idempotent(tmp_path):
    """Multiple invocations (e.g. MQTT reconnect during the same process)
    must not re-publish."""
    state_path = tmp_path / "active_procs.json"
    state_path.write_text(json.dumps({
        "msg-killed-1": {"workspace_id": "wsX", "topic_id": "tA", "agent_name": "codex"},
    }))
    client = MagicMock()
    with patch.object(mqtt_loop_module, "_ACTIVE_PROCS_PATH", state_path), \
         patch.object(mqtt_loop_module, "_inherited_already_published", False):
        mqtt_loop_module._publish_inherited_interrupts(client)
        first_call_count = client.publish.call_count
        mqtt_loop_module._publish_inherited_interrupts(client)
        assert client.publish.call_count == first_call_count


def test_publish_inherited_interrupts_noop_when_file_missing(tmp_path):
    """Fresh startup (no leftover file) is a no-op, no publish."""
    state_path = tmp_path / "active_procs.json"
    client = MagicMock()
    with patch.object(mqtt_loop_module, "_ACTIVE_PROCS_PATH", state_path), \
         patch.object(mqtt_loop_module, "_inherited_already_published", False):
        mqtt_loop_module._publish_inherited_interrupts(client)
    assert client.publish.call_count == 0


def test_publish_interrupted_all_clears_persisted_state(tmp_path):
    """SIGTERM handler path clears the snapshot so the post-shutdown restart
    doesn't double-publish agent-killed for the same messages."""
    state_path = tmp_path / "active_procs.json"
    state_path.write_text(json.dumps({"msg-abc": {"workspace_id": "ws1", "topic_id": "t1", "agent_name": "claude"}}))
    client = MagicMock()
    contexts = {"msg-abc": {"workspace_id": "ws1", "topic_id": "t1", "agent_name": "claude"}}
    with patch.object(mqtt_loop_module, "_ACTIVE_PROCS_PATH", state_path), \
         patch.dict(mqtt_loop_module._active_procs, {"msg-abc": MagicMock()}, clear=True), \
         patch.dict(mqtt_loop_module._active_contexts, contexts, clear=True), \
         patch("src.agent.mqtt_loop.os.killpg"):
        _publish_interrupted_all(client)
    assert not state_path.exists()


def test_process_prompt_sets_orch_env_with_dispatch_token(tmp_path):
    """Regression: the MQTT prompt's dispatch_token must reach the CLI
    subprocess env (via the orch-env thread-local) — without it every
    orchestrate MCP tool call is rejected by master with 422."""
    client = MagicMock()
    captured = {}

    def fake_run_claude(*args, **kwargs):
        captured.update(mqtt_loop_module._prompt_orch_env.env)
        return ("ok", None, None)

    with patch("src.agent.mqtt_loop._run_claude", side_effect=fake_run_claude):
        with patch("src.agent.mqtt_loop._ensure_worktree"):
            _process_prompt(
                client, "ws1", "t1",
                {"message_id": "m-tok", "text": "hi", "adapter": "claude-code",
                 "agent_name": "architect", "worktree": str(tmp_path),
                 "branch": "feat/t", "session_id": None,
                 "task_depth": 0, "dispatch_token": "tok-123"},
                repo_dir=str(tmp_path),
            )

    assert captured["DISPATCH_TOKEN"] == "tok-123"
    assert captured["AGENT_NAME"] == "architect"
    assert captured["PROMPT_MESSAGE_ID"] == "m-tok"
    assert captured["TASK_DEPTH"] == "0"
