"""Unit tests for _stream_claude_once and _run_claude (streaming refactor).

These tests target the post-refactor signatures described in the design doc
(docs/design/streaming-agent-reply.md).  They mock subprocess.Popen so that
scripted JSON lines are returned line-by-line, simulating real Claude
stream-json output.

Because the implementation has not yet been merged, the imports are guarded:
if the new symbols do not exist yet the tests are skipped with a clear message
so the CI gate does not break the build before the engineer lands Phase 1.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import guard — skip the whole module if the refactored symbols are absent.
# Remove the guard once _stream_claude_once is merged.
# ---------------------------------------------------------------------------
try:
    from src.agent.mqtt_loop import _stream_claude_once, _run_claude, _chunk_topic
    _STREAMING_IMPLEMENTED = True
except ImportError:
    _STREAMING_IMPLEMENTED = False

pytestmark = pytest.mark.skipif(
    not _STREAMING_IMPLEMENTED,
    reason="_stream_claude_once not yet implemented; skipping streaming tests",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_popen(lines: list[str]):
    """Return a mock Popen whose stdout yields the given lines one by one."""
    stdout_mock = io.StringIO("\n".join(lines) + "\n")
    proc = MagicMock()
    proc.stdout = stdout_mock
    proc.stderr = io.StringIO("")
    proc.wait.return_value = 0
    proc.returncode = 0
    return proc


def _events(*types_and_subtypes) -> list[dict]:
    """Build a minimal list of stream-json events for test driving."""
    evts = []
    for spec in types_and_subtypes:
        if spec == "text":
            evts.append({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]},
            })
        elif spec == "tool_use":
            evts.append({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
                ]},
            })
        elif spec == "tool_result":
            evts.append({
                "type": "user",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "content": "file listing output"}
                ]},
            })
        elif spec == "thinking":
            evts.append({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "I am thinking"}]},
            })
        elif spec == "task_progress":
            evts.append({"type": "system", "subtype": "task_progress", "description": "Running ls -la"})
        elif spec == "task_started":
            evts.append({"type": "system", "subtype": "task_started", "description": "Spawning subagent"})
        elif spec == "init":
            evts.append({"type": "system", "subtype": "init", "session_id": "sess-abc"})
        elif spec == "retry":
            evts.append({"type": "system", "subtype": "retry"})
        elif spec == "result_ok":
            evts.append({
                "type": "result",
                "result": "Done.",
                "last_response": "Done.",
                "session_id": "sess-xyz",
                "is_error": False,
            })
        elif spec == "result_error_session":
            evts.append({
                "type": "result",
                "result": "No conversation found with session ID: old-sid",
                "last_response": "No conversation found with session ID: old-sid",
                "session_id": "old-sid",
                "is_error": True,
            })
        elif spec == "result_error_generic":
            evts.append({
                "type": "result",
                "result": "Something went wrong",
                "last_response": "Something went wrong",
                "session_id": None,
                "is_error": True,
            })
    return evts


def _lines_from_events(events: list[dict]) -> list[str]:
    return [json.dumps(e) for e in events]


# ---------------------------------------------------------------------------
# _chunk_topic
# ---------------------------------------------------------------------------

def test_chunk_topic_format():
    topic = _chunk_topic("ws1", "t1")
    assert topic == "codex-slack/workspace/ws1/topic/t1/chunk"


# ---------------------------------------------------------------------------
# _stream_claude_once — chunk payloads published to MQTT
# ---------------------------------------------------------------------------

def test_stream_claude_once_publishes_chunks_with_correct_fields(tmp_path):
    """Each stream-json line becomes a published chunk with message_id, seq, event."""
    events = _events("text", "tool_use", "result_ok")
    lines = _lines_from_events(events)
    proc = _make_popen(lines)

    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        output, new_sid, transcript, is_error = _stream_claude_once(
            client=client,
            workspace_id="ws1",
            topic_id="t1",
            reply_message_id="msg-abc",
            agent_name="claude",
            worktree=str(tmp_path),
            text="hello",
            session_id=None,
            is_new_session=False,
            subagent=None,
            model=None,
            system_prompt=None,
        )

    published = client.publish.call_args_list
    # One publish per event line
    assert len(published) == len(events)

    for i, pub_call in enumerate(published):
        topic_arg = pub_call.args[0]
        payload_str = pub_call.args[1]
        assert "chunk" in topic_arg
        assert "ws1" in topic_arg
        assert "t1" in topic_arg

        payload = json.loads(payload_str)
        assert payload["message_id"] == "msg-abc"
        assert payload["agent_name"] == "claude"
        assert payload["seq"] == i
        assert "event" in payload


def test_stream_claude_once_seq_increments_monotonically(tmp_path):
    """seq goes 0, 1, 2, ... without gaps."""
    events = _events("init", "text", "tool_use", "tool_result", "result_ok")
    proc = _make_popen(_lines_from_events(events))
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-seq", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    seqs = [json.loads(c.args[1])["seq"] for c in client.publish.call_args_list]
    assert seqs == list(range(len(events)))


def test_stream_claude_once_published_event_matches_line(tmp_path):
    """The event field in the published chunk is the parsed JSON from stdout."""
    text_event = _events("text")[0]
    proc = _make_popen(_lines_from_events([text_event]))
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-ev", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    payload = json.loads(client.publish.call_args_list[0].args[1])
    assert payload["event"] == text_event


def test_stream_claude_once_qos_zero(tmp_path):
    """Chunk publishes use QoS 0 (fire-and-forget)."""
    events = _events("text", "result_ok")
    proc = _make_popen(_lines_from_events(events))
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-qos", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    for pub_call in client.publish.call_args_list:
        qos = pub_call.kwargs.get("qos", pub_call.args[2] if len(pub_call.args) > 2 else None)
        assert qos == 0, f"Expected QoS 0 but got {qos}"


# ---------------------------------------------------------------------------
# _stream_claude_once — return values
# ---------------------------------------------------------------------------

def test_stream_claude_once_transcript_contains_all_events(tmp_path):
    """transcript is a JSON array of every parsed event."""
    events = _events("init", "text", "tool_use", "result_ok")
    proc = _make_popen(_lines_from_events(events))
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        output, new_sid, transcript, is_error = _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-tr", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    assert transcript is not None
    parsed = json.loads(transcript)
    assert isinstance(parsed, list)
    assert len(parsed) == len(events)


def test_stream_claude_once_returns_output_from_result_event(tmp_path):
    events = _events("text", "result_ok")
    proc = _make_popen(_lines_from_events(events))
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        output, new_sid, transcript, is_error = _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-out", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    assert output == "Done."
    assert new_sid == "sess-xyz"
    assert is_error is False


def test_stream_claude_once_detects_is_error_from_result(tmp_path):
    events = _events("result_error_generic")
    proc = _make_popen(_lines_from_events(events))
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        output, new_sid, transcript, is_error = _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-err", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    assert is_error is True
    assert "Something went wrong" in output


def test_stream_claude_once_skips_blank_and_malformed_lines(tmp_path):
    """Blank lines are skipped; non-JSON lines emit a parse_warning chunk."""
    text_event = _events("text")[0]
    result_event = _events("result_ok")[0]
    lines = [
        "",
        "   ",
        "not-json",
        json.dumps(text_event),
        "{broken",
        json.dumps(result_event),
    ]
    proc = _make_popen(lines)
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", return_value=proc):
        output, new_sid, transcript, is_error = _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-skip", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    # 2 valid events + 2 parse_warning chunks (one per malformed line)
    assert len(client.publish.call_args_list) == 4
    published_events = [json.loads(call.args[1])["event"] for call in client.publish.call_args_list]
    warnings = [e for e in published_events if e.get("subtype") == "parse_warning"]
    assert len(warnings) == 2
    assert warnings[0]["line"] == "not-json"
    assert warnings[1]["line"] == "{broken"
    assert is_error is False


def test_stream_claude_once_handles_file_not_found(tmp_path):
    client = MagicMock()
    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=FileNotFoundError()):
        output, new_sid, transcript, is_error = _stream_claude_once(
            client=client, workspace_id="ws1", topic_id="t1",
            reply_message_id="msg-fnf", agent_name="claude",
            worktree=str(tmp_path), text="hi",
            session_id=None, is_new_session=False,
            subagent=None, model=None, system_prompt=None,
        )

    assert is_error is True
    assert "not found" in output


# ---------------------------------------------------------------------------
# _run_claude — retry path with streaming
# ---------------------------------------------------------------------------

def test_run_claude_retry_publishes_synthetic_retry_chunk(tmp_path):
    """On session-not-found, a {type:system, subtype:retry} chunk is published
    before the second attempt begins, and seq continues across both attempts."""
    error_events = _events("result_error_session")
    ok_events = _events("text", "result_ok")

    error_proc = _make_popen(_lines_from_events(error_events))
    ok_proc = _make_popen(_lines_from_events(ok_events))

    procs = iter([error_proc, ok_proc])
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=lambda *a, **kw: next(procs)):
        output, new_sid, transcript = _run_claude(
            client=client,
            workspace_id="ws1",
            topic_id="t1",
            reply_message_id="msg-retry",
            agent_name="claude",
            worktree=str(tmp_path),
            text="hi",
            session_id="old-sid",
            is_new_session=False,
            subagent=None,
            model=None,
            system_prompt=None,
        )

    all_publishes = client.publish.call_args_list
    all_payloads = [json.loads(c.args[1]) for c in all_publishes]

    # There must be a retry chunk
    retry_payloads = [p for p in all_payloads if p.get("event", {}).get("type") == "system"
                      and p.get("event", {}).get("subtype") == "retry"]
    assert len(retry_payloads) == 1, "Expected exactly one retry chunk published"

    # seq must be strictly increasing across the retry boundary — no reset
    seqs = [p["seq"] for p in all_payloads]
    assert seqs == sorted(seqs), "seq must be monotonically increasing across retry"
    assert len(set(seqs)) == len(seqs), "seq must have no duplicates across retry"


def test_run_claude_retry_seq_continues_not_reset(tmp_path):
    """seq in the second attempt picks up where the first attempt left off."""
    error_events = _events("init", "result_error_session")   # seq 0, 1
    ok_events = _events("text", "result_ok")                  # seq 3, 4 (after retry at seq 2)

    error_proc = _make_popen(_lines_from_events(error_events))
    ok_proc = _make_popen(_lines_from_events(ok_events))
    procs = iter([error_proc, ok_proc])
    client = MagicMock()

    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=lambda *a, **kw: next(procs)):
        _run_claude(
            client=client,
            workspace_id="ws1",
            topic_id="t1",
            reply_message_id="msg-seqcont",
            agent_name="claude",
            worktree=str(tmp_path),
            text="hi",
            session_id="old-sid",
            is_new_session=False,
            subagent=None,
            model=None,
            system_prompt=None,
        )

    seqs = [json.loads(c.args[1])["seq"] for c in client.publish.call_args_list]
    # Must be strictly increasing with no resets
    assert seqs == sorted(seqs)
    assert seqs[0] == 0


def test_run_claude_no_retry_without_session_id(tmp_path):
    """If there is no session_id, session-not-found errors do not trigger retry."""
    error_events = _events("result_error_session")
    proc = _make_popen(_lines_from_events(error_events))
    client = MagicMock()
    popen_calls = []

    def counting_popen(*args, **kwargs):
        popen_calls.append(1)
        return proc

    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=counting_popen):
        _run_claude(
            client=client,
            workspace_id="ws1",
            topic_id="t1",
            reply_message_id="msg-noretry",
            agent_name="claude",
            worktree=str(tmp_path),
            text="hi",
            session_id=None,
            is_new_session=False,
            subagent=None,
            model=None,
            system_prompt=None,
        )

    assert len(popen_calls) == 1, "Should not retry when session_id is None"


def test_run_claude_retry_uses_new_session_flag(tmp_path):
    """The second Popen invocation must include --session-id (fresh session),
    not --resume, matching the existing retry logic in _run_claude."""
    error_events = _events("result_error_session")
    ok_events = _events("result_ok")

    error_proc = _make_popen(_lines_from_events(error_events))
    ok_proc = _make_popen(_lines_from_events(ok_events))
    procs = iter([error_proc, ok_proc])

    captured_cmds: list[list[str]] = []
    client = MagicMock()

    def capturing_popen(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        return next(procs)

    with patch("src.agent.mqtt_loop.subprocess.Popen", side_effect=capturing_popen):
        _run_claude(
            client=client,
            workspace_id="ws1",
            topic_id="t1",
            reply_message_id="msg-flag",
            agent_name="claude",
            worktree=str(tmp_path),
            text="hi",
            session_id="old-sid",
            is_new_session=False,
            subagent=None,
            model=None,
            system_prompt=None,
        )

    assert len(captured_cmds) == 2, "Expected two Popen calls"
    # First call uses --resume
    assert "--resume" in captured_cmds[0]
    # Second call uses --session-id (fresh session, not --resume)
    assert "--session-id" in captured_cmds[1]
    assert "--resume" not in captured_cmds[1]
