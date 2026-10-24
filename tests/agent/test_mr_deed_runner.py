"""Tests for the in-process mr-deed adapter (ADR-0018, test plan mr-deed-adapter-p1).

All turns use mr-deed's deterministic tool-call model: no network, no API keys.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.agent.mqtt_loop as mqtt_loop_module
from src.agent import mr_deed_runner as runner
from src.agent.mqtt_loop import _process_prompt

from mr_deed.exceptions import FormatError
from mr_deed.models.test_models import DeterministicToolcallModel, make_toolcall_output

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bash(call_id: str, command: str) -> dict:
    call = {"id": call_id, "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": command})}}
    action = {"name": "bash", "arguments": {"command": command}, "command": command, "tool_call_id": call_id}
    return make_toolcall_output(f"running {call_id}", [call], [action])


def _submit(call_id: str, reply: str) -> dict:
    return _bash(call_id, f"printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\n%s\\n' \"{reply}\"")


def _models(*outputs: dict):
    def factory(cfg: dict) -> DeterministicToolcallModel:
        return DeterministicToolcallModel(
            outputs=list(outputs), cost_per_call=0.0,
            **{k: v for k, v in cfg.items() if k == "observation_template"},
        )
    return factory


_CONFIG = """
agent:
  system_template: "SYS"
  instance_template: "{{ task }}"
  cost_limit: 0
model:
  observation_template: "{{ output.output }}"
  format_error_template: "{{ error }}"
"""


def _turn(tmp_path: Path, *outputs: dict, events: list | None = None, **overrides):
    kwargs = dict(
        text="do it",
        worktree=str(tmp_path),
        reply_message_id="reply-1",
        session_id="sess-1",
        is_new_session=True,
        session_scope="topic",
        config_name=None,
        agent_config=_CONFIG,
        model=None,
        system_prompt=None,
        turn_env={"DISPATCH_TOKEN": "tok-1"},
        publish_event=(events.append if events is not None else lambda e: None),
        sessions_dir=tmp_path / "sessions",
        model_factory=_models(*outputs),
    )
    kwargs.update(overrides)
    return runner.run_turn(**kwargs)


# ── Packaging and import hygiene (MD-U-01..04) ───────────────────────────────

def test_requirements_pin_mr_deed_v0_1_0():
    lines = [l.strip() for l in (REPO_ROOT / "requirements.txt").read_text().splitlines()]
    deed = [l for l in lines if l.startswith("mr-deed")]
    assert deed == ["mr-deed @ git+https://github.com/pandazxx/mr-deed@v0.1.0"]


def test_runner_import_has_no_env_side_effects_and_skips_cli(tmp_path):
    (tmp_path / ".env").write_text("PROBE_FROM_DOTENV=leaked\n")
    script = (
        "import os, sys, json\n"
        "before = dict(os.environ)\n"
        "from src.agent import mr_deed_runner\n"
        "mr_deed_runner._import_mr_deed()\n"
        "after = dict(os.environ)\n"
        "after.pop('LITELLM_MODE', None)\n"
        "print(json.dumps({'changed': after != before, 'run': 'mr_deed.run' in sys.modules}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_MODE"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"changed": False, "run": False}


# ── Config resolution (MD-U-24..30) ──────────────────────────────────────────

def test_tier0_agent_config_wins(tmp_path):
    (tmp_path / "default.yaml").write_text("agent: {system_template: shipped}\n")
    config, source = runner.resolve_config("agent: {system_template: staff}\n", "default", tmp_path)
    assert config["agent"]["system_template"] == "staff"
    assert source == "staff agent_config"


def test_tier2_shipped_config(tmp_path):
    (tmp_path / "helper.yaml").write_text("agent: {system_template: shipped}\n")
    config, source = runner.resolve_config(None, "helper", tmp_path)
    assert config["agent"]["system_template"] == "shipped"
    assert source == str(tmp_path / "helper.yaml")


def test_tier3_builtin_config(tmp_path):
    config, source = runner.resolve_config("", "mini", tmp_path)
    assert source.endswith("mr_deed/config/mini.yaml")
    assert "system_template" in config["agent"]


@pytest.mark.parametrize("name", ["", None])
def test_empty_name_uses_shipped_default(name):
    config, source = runner.resolve_config(None, name)
    assert source == str(runner.CONFIG_DIR / "default.yaml")
    assert config["agent"]["cost_limit"] > 0


def test_shipped_default_is_tool_call_config():
    config, _ = runner.resolve_config(None, "default")
    assert "model_class" not in (config.get("model") or {})
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in config["agent"]["system_template"]


@pytest.mark.parametrize("name", ["nonexistent", "../secrets", ".hidden"])
def test_unknown_or_unsafe_config_name_is_error_reply(tmp_path, name):
    reply, session_id, transcript = _turn(tmp_path, agent_config=None, config_name=name, config_dir=tmp_path)
    assert reply.startswith(f"(mr-deed config '{name}' is invalid:")
    assert session_id is None and transcript is None


def test_agent_config_mr_deed_rejects_is_error_reply(tmp_path):
    reply, _, _ = _turn(tmp_path, agent_config="agent: {system_template: [not, a, string]}\n")
    assert reply.startswith("(mr-deed config 'staff agent_config' is invalid:")


# ── Overrides (MD-U-31..35) ──────────────────────────────────────────────────

def test_staff_model_overrides_model_name():
    config = runner.apply_overrides({"model": {"model_name": "X"}}, "Y", None)
    assert config["model"]["model_name"] == "Y"


def test_system_prompt_appended_as_literal_text(tmp_path):
    events: list = []
    reply, _, _ = _turn(tmp_path, _submit("c1", "ok"), events=events, system_prompt="Be {{ brief }}")
    traj = json.loads((tmp_path / "sessions" / "sess-1.traj.json").read_text())
    assert reply == "ok"
    assert traj["messages"][0]["content"] == "SYS\n\nBe {{ brief }}"


def test_system_prompt_with_jinja_delimiters_is_literal(tmp_path):
    prompt = "Docs: {% endraw %}{{ undefined_var }}{% raw %} and {{ task }}"
    reply, _, _ = _turn(tmp_path, _submit("c1", "ok"), system_prompt=prompt)
    traj = json.loads((tmp_path / "sessions" / "sess-1.traj.json").read_text())
    assert reply == "ok"
    assert traj["messages"][0]["content"] == f"SYS\n\n{prompt}"


def test_resume_without_system_prompt_still_renders(tmp_path):
    _turn(tmp_path, _submit("c1", "first"), system_prompt="extra")
    reply, _, _ = _turn(tmp_path, _submit("c2", "second"), is_new_session=False, reply_message_id="reply-2")
    assert reply == "second"


def test_ping_alive_until_response_queued():
    client = MagicMock()
    with mqtt_loop_module._active_procs_lock:
        mqtt_loop_module._active_contexts["reply-post"] = {"workspace_id": "ws1", "topic_id": "t1", "agent_name": "d"}
    try:
        msg = MagicMock()
        msg.topic = "codex-slack/workspace/ws1/topic/t1/ping"
        msg.payload = json.dumps({"message_id": "reply-post"}).encode()
        mqtt_loop_module._on_message(client, {}, msg)
    finally:
        with mqtt_loop_module._active_procs_lock:
            mqtt_loop_module._active_contexts.pop("reply-post", None)
    assert json.loads(client.publish.call_args.args[1])["alive"] is True


def test_no_system_prompt_keeps_template():
    config = runner.apply_overrides({"agent": {"system_template": "SYS"}}, None, None)
    assert config["agent"]["system_template"] == "SYS"


def test_environment_forced_local_in_worktree(tmp_path):
    env = runner.build_environment(
        {"environment": {"environment_class": "docker", "cwd": "/elsewhere", "env": {"PAGER": "cat"}}},
        str(tmp_path), {"DISPATCH_TOKEN": "t"},
    )
    assert type(env).__name__ == "LocalEnvironment"
    assert env.config.cwd == str(tmp_path)
    assert env.config.env == {"PAGER": "cat", "DISPATCH_TOKEN": "t"}


# ── Sessions (MD-U-36..43) ───────────────────────────────────────────────────

def test_new_session_saves_trajectory(tmp_path):
    reply, session_id, _ = _turn(tmp_path, _submit("c1", "first"))
    path = tmp_path / "sessions" / "sess-1.traj.json"
    assert (reply, session_id) == ("first", "sess-1")
    assert [m["role"] for m in json.loads(path.read_text())["messages"]][:2] == ["system", "user"]


def test_resume_continues_saved_conversation(tmp_path):
    _turn(tmp_path, _submit("c1", "first"))
    reply, _, _ = _turn(tmp_path, _submit("c2", "second"), is_new_session=False, text="next",
                        reply_message_id="reply-2")
    messages = json.loads((tmp_path / "sessions" / "sess-1.traj.json").read_text())["messages"]
    assert reply == "second"
    assert [m["role"] for m in messages].count("system") == 1
    assert messages[0]["content"] == "SYS"
    assert [m["content"] for m in messages if m["role"] == "user"] == ["do it", "next"]


def test_resume_applies_current_config(tmp_path):
    _turn(tmp_path, _submit("c1", "first"))
    changed = _CONFIG.replace("cost_limit: 0", "cost_limit: 0\n  step_limit: 1")
    reply, _, _ = _turn(tmp_path, _bash("c2", "true"), _submit("c3", "never"), is_new_session=False,
                        agent_config=changed, reply_message_id="reply-2")
    assert reply.startswith("(mr-deed hit a limit: step_limit=1")


def test_resume_with_missing_file_starts_new(tmp_path):
    reply, _, _ = _turn(tmp_path, _submit("c1", "fresh"), is_new_session=False)
    assert reply == "fresh"


def test_corrupted_trajectory_is_left_untouched(tmp_path):
    path = tmp_path / "sessions" / "sess-1.traj.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    reply, session_id, _ = _turn(tmp_path, _submit("c1", "x"), is_new_session=False)
    assert reply.startswith(f"(mr-deed session file {path} could not be loaded")
    assert session_id is None
    assert path.read_text() == "{not json"


def test_sessions_live_on_the_mr_deed_volume():
    from src.master import agent_runner
    import inspect
    assert runner.SESSIONS_DIR == Path("/home/appuser/.mr-deed/sessions")
    assert '"/home/appuser/.mr-deed"' in inspect.getsource(agent_runner.spawn_agent)


def test_unwritable_session_dir_is_named_error(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    reply, session_id, _ = _turn(tmp_path, _submit("c1", "x"), sessions_dir=blocker / "sessions")
    assert reply.startswith(f"(mr-deed cannot use its session directory {blocker / 'sessions'}:")
    assert session_id is None


def test_scope_none_does_not_persist(tmp_path):
    reply, session_id, _ = _turn(tmp_path, _submit("c1", "ok"), session_scope="none")
    assert (reply, session_id) == ("ok", None)
    assert not (tmp_path / "sessions").exists()


def test_trajectory_saved_after_each_step(tmp_path):
    seen: list = []
    path = tmp_path / "sessions" / "sess-1.traj.json"

    def record(event):
        if event["type"] == "mr_deed.action" and path.exists():
            seen.append(len(json.loads(path.read_text())["messages"]))

    _turn(tmp_path, _bash("c1", "true"), _submit("c2", "ok"), publish_event=record)
    # By the second action, step one (assistant + observation) is already on disk.
    assert seen and seen[-1] >= 4


# ── Streaming (MD-U-44..54) ──────────────────────────────────────────────────

def test_events_published_with_mr_deed_types(tmp_path):
    events: list = []
    _turn(tmp_path, _bash("c1", "echo hi"), _submit("c2", "ok"), events=events)
    assert [e["type"] for e in events] == [
        "mr_deed.assistant", "mr_deed.action", "mr_deed.observation",
        "mr_deed.assistant", "mr_deed.action", "mr_deed.observation",
        "mr_deed.exit",
    ]
    assert events[1]["message"]["command"] == "echo hi"
    assert events[2]["message"]["content"].strip() == "hi"
    assert events[-1]["message"]["extra"]["exit_status"] == "Submitted"


def test_format_error_event_published(tmp_path):
    class FormatErrorOnce(DeterministicToolcallModel):
        def query(self, messages, **kwargs):
            if not getattr(self, "_failed", False):
                self._failed = True
                raise FormatError({"role": "user", "content": "bad format", "extra": {"interrupt_type": "FormatError"}})
            return super().query(messages, **kwargs)

    events: list = []
    _turn(tmp_path, events=events, model_factory=lambda cfg: FormatErrorOnce(outputs=[_submit("c1", "ok")]))
    assert events[0]["type"] == "mr_deed.format_error"


def test_raw_provider_response_not_published(tmp_path):
    events: list = []
    _turn(tmp_path, _submit("c1", "ok"), events=events)
    assistant = events[0]["message"]
    assert "response" not in assistant.get("extra", {})


def test_transcript_is_published_event_list(tmp_path):
    events: list = []
    _, _, transcript = _turn(tmp_path, _submit("c1", "ok"), events=events)
    assert json.loads(transcript) == json.loads(json.dumps(events))


def test_publish_failure_does_not_abort_turn(tmp_path):
    def broken(event):
        raise ConnectionError("broker down")

    reply, _, transcript = _turn(tmp_path, _submit("c1", "still ok"), publish_event=broken)
    assert reply == "still ok"
    assert len(json.loads(transcript)) == 4


# ── Exit mapping (MD-U-55..62) ───────────────────────────────────────────────

def test_step_limit_reply(tmp_path):
    cfg = _CONFIG.replace("cost_limit: 0", "cost_limit: 0\n  step_limit: 1")
    reply, _, _ = _turn(tmp_path, _bash("c1", "true"), _submit("c2", "x"), agent_config=cfg)
    assert reply.startswith("(mr-deed hit a limit: step_limit=1, cost_limit=0.0, steps=1, cost=$")


def test_wall_time_limit_reply(tmp_path):
    cfg = _CONFIG.replace("cost_limit: 0", "cost_limit: 0\n  wall_time_limit_seconds: 1")
    reply, _, _ = _turn(tmp_path, _bash("c1", "sleep 1.2"), _submit("c2", "x"), agent_config=cfg)
    assert reply == "(mr-deed timed out after 1s)"


def test_repeated_format_error_reply(tmp_path):
    class AlwaysBad(DeterministicToolcallModel):
        def query(self, messages, **kwargs):
            raise FormatError({"role": "user", "content": "bad", "extra": {"interrupt_type": "FormatError"}})

    reply, _, _ = _turn(tmp_path, model_factory=lambda cfg: AlwaysBad(outputs=[]))
    assert reply == "(mr-deed could not produce a parseable action)"


def test_provider_exception_reply(tmp_path):
    import litellm

    class Unauthorized(DeterministicToolcallModel):
        def query(self, messages, **kwargs):
            raise litellm.AuthenticationError(message="bad key", llm_provider="openai", model="m")

    reply, session_id, _ = _turn(tmp_path, model_factory=lambda cfg: Unauthorized(outputs=[]))
    assert reply.startswith("(mr-deed model call failed: AuthenticationError:")
    assert session_id == "sess-1"


def test_unexpected_exception_reply(tmp_path):
    class Boom(DeterministicToolcallModel):
        def query(self, messages, **kwargs):
            raise RuntimeError("boom")

    reply, _, _ = _turn(tmp_path, model_factory=lambda cfg: Boom(outputs=[]))
    assert reply == "(mr-deed crashed: RuntimeError)"
    assert not runner.is_running("reply-1")


def test_missing_model_name_is_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MRDEED_MODEL_NAME", raising=False)
    reply, _, _ = _turn(tmp_path, model_factory=None)
    assert reply.startswith("(mr-deed config 'default' is invalid:") or reply.startswith(
        "(mr-deed config 'staff agent_config' is invalid:")
    assert "model" in reply.lower()


# ── Cancel (MD-U-63..69) ─────────────────────────────────────────────────────

def _run_in_thread(fn):
    result: dict = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", fn()))
    thread.start()
    return thread, result


def test_registered_while_running_and_removed_after(tmp_path):
    seen: list = []
    _turn(tmp_path, _submit("c1", "ok"), publish_event=lambda e: seen.append(runner.is_running("reply-1")))
    assert all(seen) and seen
    assert not runner.is_running("reply-1")


def test_cancel_kills_running_command(tmp_path):
    started = threading.Event()

    def on_event(event):
        if event["type"] == "mr_deed.action":
            started.set()

    thread, result = _run_in_thread(lambda: _turn(
        tmp_path, _bash("c1", "sleep 30"), _submit("c2", "x"),
        publish_event=on_event, reply_message_id="reply-cancel"))
    assert started.wait(10)
    time.sleep(0.3)
    begun = time.monotonic()
    assert runner.cancel("reply-cancel") is True
    thread.join(10)
    assert time.monotonic() - begun < 5
    assert result["value"][0] == "(cancelled by user)"


def test_cancel_before_registration_is_applied(tmp_path):
    assert runner.cancel("reply-early") is False
    reply, _, _ = _turn(tmp_path, _submit("c1", "x"), reply_message_id="reply-early")
    assert reply == "(cancelled by user)"


def test_cancel_does_not_leak_into_next_turn(tmp_path):
    runner.cancel("reply-a")
    _turn(tmp_path, _submit("c1", "x"), reply_message_id="reply-a")
    reply, _, _ = _turn(tmp_path, _submit("c2", "next"), is_new_session=False, reply_message_id="reply-b")
    assert reply == "next"


def test_cancel_message_reaches_running_agent():
    agent = MagicMock()
    runner._register("reply-mqtt", agent)
    try:
        msg = MagicMock()
        msg.topic = "codex-slack/workspace/ws1/topic/t1/cancel"
        msg.payload = json.dumps({"message_id": "reply-mqtt"}).encode()
        mqtt_loop_module._on_message(MagicMock(), {}, msg)
    finally:
        runner._unregister("reply-mqtt")
    agent.cancel.assert_called_once()


def test_ping_reports_in_process_turn_alive():
    runner._register("reply-ping", MagicMock())
    client = MagicMock()
    try:
        msg = MagicMock()
        msg.topic = "codex-slack/workspace/ws1/topic/t1/ping"
        msg.payload = json.dumps({"message_id": "reply-ping"}).encode()
        mqtt_loop_module._on_message(client, {}, msg)
    finally:
        runner._unregister("reply-ping")
    pong = json.loads(client.publish.call_args.args[1])
    assert pong == {"message_id": "reply-ping", "alive": True}


# ── Per-turn env transport, one test per hop (MD-U-70..75) ───────────────────

_PROMPT = {
    "message_id": "prompt-1", "text": "hi", "adapter": "mr-deed", "agent_name": "deed",
    "session_id": "sess-1", "is_new_session": True, "session_scope": "topic",
    "subagent": "helper", "agent_config": _CONFIG, "model": "m", "system_prompt": "extra",
    "task_depth": 1, "dispatch_token": "tok-hop", "master_url": "http://master:9999",
}


def _process(tmp_path, run_turn):
    client = MagicMock()
    with patch("src.agent.mqtt_loop.mr_deed_runner.run_turn", side_effect=run_turn):
        _process_prompt(client, "ws-1", "topic-1", {**_PROMPT, "worktree": str(tmp_path)}, repo_dir=str(tmp_path))
    return client


def test_process_prompt_mr_deed_sets_orch_env(tmp_path):
    captured = {}

    def fake(**kwargs):
        captured.update(mqtt_loop_module._prompt_orch_env.env)
        return "ok", None, None

    _process(tmp_path, fake)
    assert captured == {"AGENT_NAME": "deed", "PROMPT_MESSAGE_ID": "prompt-1", "TASK_DEPTH": "1",
                        "DISPATCH_TOKEN": "tok-hop"}


def test_run_mr_deed_builds_env_dict_from_thread_local(tmp_path):
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return "ok", "sess-1", None

    client = _process(tmp_path, fake)
    assert captured["turn_env"] == {
        "AGENT_NAME": "deed", "PROMPT_MESSAGE_ID": "prompt-1", "TASK_DEPTH": "1", "DISPATCH_TOKEN": "tok-hop",
        "WORKSPACE_ID": "ws-1", "TOPIC_ID": "topic-1", "MASTER_URL": "http://master:9999",
    }
    assert (captured["config_name"], captured["agent_config"], captured["model"], captured["system_prompt"]) == (
        "helper", _CONFIG, "m", "extra")
    response = json.loads(next(c.args[1] for c in client.publish.call_args_list if c.args[0].endswith("/response")))
    assert (response["last_response"], response["session_id"]) == ("ok", "sess-1")


def test_run_mr_deed_passes_env_to_local_environment(tmp_path):
    env = runner.build_environment({}, str(tmp_path), {"DISPATCH_TOKEN": "tok-x", "TOPIC_ID": "t"})
    assert env.config.env == {"DISPATCH_TOKEN": "tok-x", "TOPIC_ID": "t"}


def test_bash_sees_dispatch_token_in_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPATCH_TOKEN", raising=False)
    events: list = []
    _turn(tmp_path, _bash("c1", "echo token=$DISPATCH_TOKEN"), _submit("c2", "ok"), events=events,
          turn_env={"DISPATCH_TOKEN": "tok-bash"})
    assert events[2]["message"]["content"].strip() == "token=tok-bash"
    assert "DISPATCH_TOKEN" not in os.environ


def test_chunks_published_on_topic_chunk_channel(tmp_path):
    def fake(**kwargs):
        kwargs["publish_event"]({"type": "mr_deed.assistant", "message": {"content": "x"}})
        return "ok", None, None

    client = _process(tmp_path, fake)
    chunk = next(c for c in client.publish.call_args_list if c.args[0].endswith("/chunk"))
    assert chunk.args[0] == "codex-slack/workspace/ws-1/topic/topic-1/chunk"
    body = json.loads(chunk.args[1])
    assert (body["agent_name"], body["seq"], body["event"]["type"]) == ("deed", 0, "mr_deed.assistant")


# ── Concurrency (MD-U-76..77) ────────────────────────────────────────────────

def test_two_mr_deed_turns_in_one_process_do_not_cross_contaminate(tmp_path):
    barrier = threading.Barrier(2)

    def turn(name):
        events: list = []
        command = "echo token=$DISPATCH_TOKEN"
        reply = _turn(tmp_path, _bash("c1", f"sleep 0.2; {command}"), _submit("c2", name), events=events,
                      reply_message_id=f"reply-{name}", session_id=f"sess-{name}",
                      turn_env={"DISPATCH_TOKEN": f"tok-{name}"},
                      publish_event=lambda e: (events.append(e), barrier.wait(30) if e["type"] == "mr_deed.action"
                                               and len(events) == 2 else None))
        return reply, events

    threads = [_run_in_thread(lambda n=n: turn(n)) for n in ("a", "b")]
    for thread, _ in threads:
        thread.join(15)
    for name, (_, result) in zip(("a", "b"), threads):
        reply, events = result["value"]
        assert reply[0] == name
        assert events[2]["message"]["content"].strip() == f"token=tok-{name}"
        assert (tmp_path / "sessions" / f"sess-{name}.traj.json").exists()


# ── Backward compatibility (MD-U-78..79) ─────────────────────────────────────

@pytest.mark.parametrize("adapter,target", [("claude-code", "_run_claude"), ("codex", "_run_codex")])
def test_other_adapters_do_not_reach_mr_deed(tmp_path, adapter, target):
    with patch(f"src.agent.mqtt_loop.{target}", return_value=("ok", None, None)) as other, \
         patch("src.agent.mqtt_loop.mr_deed_runner.run_turn") as deed:
        _process_prompt(MagicMock(), "ws", "t", {**_PROMPT, "adapter": adapter, "worktree": str(tmp_path)},
                        repo_dir=str(tmp_path))
    other.assert_called_once()
    deed.assert_not_called()
