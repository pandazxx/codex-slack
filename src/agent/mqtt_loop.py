"""MQTT-driven prompt processor for the v3 agent worker."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger(__name__)

_TOPIC_PARTS = 6  # codex-slack/workspace/{wid}/topic/{tid}/prompt
_LLM_TIMEOUT = None  # no timeout — claude/codex can run as long as needed

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-llm")

# Tracks active claude subprocesses by reply_message_id so they can be killed on cancel.
_active_procs: dict[str, subprocess.Popen] = {}  # type: ignore[type-arg]
_active_procs_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_prompt_topic(raw: str) -> tuple[str, str] | None:
    parts = raw.split("/")
    if len(parts) != _TOPIC_PARTS or parts[5] != "prompt":
        return None
    return parts[2], parts[4]  # workspace_id, topic_id


def _parse_cancel_topic(raw: str) -> tuple[str, str] | None:
    parts = raw.split("/")
    if len(parts) != _TOPIC_PARTS or parts[5] != "cancel":
        return None
    return parts[2], parts[4]  # workspace_id, topic_id


def _status_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/status"


def _response_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/response"


def _chunk_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/chunk"


def _ensure_worktree(repo_dir: str, worktree_path: str, branch: str, repo_ref: str = "", base_sha: str = "") -> None:
    if Path(worktree_path).exists():
        return
    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)
    # Prefer the pre-resolved SHA — it always exists in the local clone regardless of
    # which branch the agent repo was cloned from.  Fall back to origin/<repo_ref> so
    # remote-tracking refs resolve even when no local branch of that name exists.
    commit_ish = base_sha or (f"origin/{repo_ref}" if repo_ref else "")
    base = [commit_ish] if commit_ish else []
    try:
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", worktree_path, "-b", branch] + base,
            capture_output=True, text=True, check=True,
        )
        LOGGER.info("agent.worktree_created path=%s branch=%s base=%s", worktree_path, branch, commit_ish or "HEAD")
    except subprocess.CalledProcessError:
        # Branch already exists — check out without -b
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", worktree_path, branch],
            capture_output=True, text=True, check=True,
        )
        LOGGER.info("agent.worktree_reused path=%s branch=%s", worktree_path, branch)


_SESSION_NOT_FOUND = "No conversation found with session ID"


def _fetch_attachment(master_url: str, attachment_id: str, filename: str, worktree: str) -> None:
    url = f"{master_url}/api/attachments/{attachment_id}/download"
    dest = Path(worktree) / filename
    with urllib.request.urlopen(url) as resp:
        dest.write_bytes(resp.read())
    LOGGER.info("agent.attachment_fetched id=%s filename=%s", attachment_id, filename)


def _stream_claude_once(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    session_id: str | None,
    is_new_session: bool,
    subagent: str | None,
    model: str | None,
    system_prompt: str | None,
    seq_start: int = 0,
) -> tuple[str, str | None, str | None, bool]:
    """Stream Claude stdout line by line, publishing each event as an MQTT chunk.

    Returns (output, new_session_id, transcript, is_error).
    """
    cmd = ["claude", "--print", "--verbose", "--output-format", "stream-json", "--dangerously-skip-permissions"]
    if session_id:
        if is_new_session:
            cmd += ["--session-id", session_id]
        else:
            cmd += ["--resume", session_id]
    if model:
        cmd += ["--model", model]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    if subagent:
        cmd += ["--agent", subagent]
    cmd.append(text)
    chunk_topic = _chunk_topic(workspace_id, topic_id)
    events: list[dict] = []
    new_session_id: str | None = None
    output: str | None = None
    is_error = False
    seq = seq_start
    outputs: list[str] = []
    proc = None
    try:
        proc = subprocess.Popen(
            cmd, cwd=worktree,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        with _active_procs_lock:
            _active_procs[reply_message_id] = proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                events.append(event)
                client.publish(chunk_topic, json.dumps({
                    "message_id": reply_message_id,
                    "agent_name": agent_name,
                    "seq": seq,
                    "event": event,
                }), qos=0)
                LOGGER.debug("agent.llm_chunk topic_id=%s seq=%d type=%s", topic_id, seq, event.get("type"))
                seq += 1
                if event.get("type") == "result":
                    new_session_id = event.get("session_id")
                    result_text = event.get("result") or event.get("last_response")
                    if result_text:
                        outputs.append(result_text)
                    if event.get("is_error"):
                        is_error = True
            proc.wait()
        finally:
            with _active_procs_lock:
                _active_procs.pop(reply_message_id, None)
        output = "\n\n---\n\n".join(outputs) if outputs else None
        if not output:
            err = (proc.stderr.read() or "").strip()
            output = err or "(no output)"
        transcript = json.dumps(events) if events else None
        LOGGER.info(
            "agent.llm_done topic_id=%s chunks=%d chars=%d",
            topic_id, seq - seq_start, len(output),
        )
        return output, new_session_id, transcript, is_error
    except FileNotFoundError:
        return "(claude CLI not found in agent container)", None, None, True
    except Exception as exc:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        with _active_procs_lock:
            _active_procs.pop(reply_message_id, None)
        return f"(claude error: {exc})", None, None, True


def _run_claude(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    session_id: str | None,
    is_new_session: bool,
    subagent: str | None,
    model: str | None,
    system_prompt: str | None,
) -> tuple[str, str | None, str | None]:
    output, new_session_id, transcript, is_error = _stream_claude_once(
        client, workspace_id, topic_id, reply_message_id, agent_name,
        worktree, text, session_id, is_new_session, subagent, model, system_prompt,
        seq_start=0,
    )
    if not is_new_session and session_id and is_error and _SESSION_NOT_FOUND in (output or ""):
        LOGGER.warning("agent.session_expired sid=%s retrying_as_new", session_id)
        first_event_count = len(json.loads(transcript)) if transcript else 0
        seq = first_event_count
        client.publish(_chunk_topic(workspace_id, topic_id), json.dumps({
            "message_id": reply_message_id,
            "agent_name": agent_name,
            "seq": seq,
            "event": {"type": "system", "subtype": "retry"},
        }), qos=0)
        seq += 1
        output, new_session_id, transcript, _ = _stream_claude_once(
            client, workspace_id, topic_id, reply_message_id, agent_name,
            worktree, text, session_id, True, subagent, model, system_prompt,
            seq_start=seq,
        )
    return output, new_session_id, transcript


def _stream_codex_once(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    model: str | None,
    seq_start: int = 0,
) -> tuple[str, str | None, bool]:
    """Stream Codex stdout line by line, publishing each event as an MQTT chunk.

    Returns (output, transcript, is_error).
    """
    fd, output_file = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    cmd = [
        "codex", "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "-s", "danger-full-access",
        "--ephemeral",
        "-o", output_file,
    ]
    if model:
        cmd += ["-m", model]
    cmd.append(text)
    chunk_topic = _chunk_topic(workspace_id, topic_id)
    events: list[dict] = []
    is_error = False
    seq = seq_start
    fallback_outputs: list[str] = []
    proc = None
    try:
        proc = subprocess.Popen(
            cmd, cwd=worktree,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                event = {"type": "output", "content": line}
            events.append(event)
            client.publish(chunk_topic, json.dumps({
                "message_id": reply_message_id,
                "agent_name": agent_name,
                "seq": seq,
                "event": event,
            }), qos=0)
            LOGGER.debug("agent.codex_chunk topic_id=%s seq=%d type=%s", topic_id, seq, event.get("type"))
            seq += 1
            ev_type = event.get("type", "")
            if ev_type == "turn.completed":
                final = event.get("output_text") or event.get("last_message")
                if final:
                    fallback_outputs.append(str(final))
            elif ev_type == "turn.failed":
                is_error = True
                err_msg = (event.get("error") or {}).get("message", "")
                if err_msg:
                    fallback_outputs.append(f"(codex error: {err_msg})")
        proc.wait()
        if proc.returncode and proc.returncode != 0 and not is_error:
            is_error = True
        output = None
        try:
            content = Path(output_file).read_text(encoding="utf-8").strip()
            if content:
                output = content
        except Exception:
            pass
        finally:
            try:
                Path(output_file).unlink()
            except Exception:
                pass
        if not output:
            output = "\n\n---\n\n".join(fallback_outputs) if fallback_outputs else None
        if not output:
            err = (proc.stderr.read() or "").strip()
            output = err or "(no output)"
        transcript = json.dumps(events) if events else None
        LOGGER.info(
            "agent.codex_done topic_id=%s chunks=%d chars=%d",
            topic_id, seq - seq_start, len(output),
        )
        return output, transcript, is_error
    except FileNotFoundError:
        try:
            Path(output_file).unlink()
        except Exception:
            pass
        return "(codex CLI not found in agent container)", None, True
    except Exception as exc:
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
        try:
            Path(output_file).unlink()
        except Exception:
            pass
        return f"(codex error: {exc})", None, True


def _run_codex(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    reply_message_id: str,
    agent_name: str,
    worktree: str,
    text: str,
    model: str | None,
) -> tuple[str, str | None, str | None]:
    output, transcript, _ = _stream_codex_once(
        client, workspace_id, topic_id, reply_message_id, agent_name,
        worktree, text, model,
    )
    return output, None, transcript


def _process_prompt(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    payload: dict,  # type: ignore[type-arg]
    repo_dir: str,
) -> None:
    message_id = payload.get("message_id") or str(uuid.uuid4())
    reply_message_id = str(uuid.uuid4())
    agent_name = payload.get("agent_name", "claude")
    worktree = payload.get("worktree", "")
    branch = payload.get("branch", "")
    repo_ref = payload.get("repo_ref", "")
    base_sha = payload.get("base_sha", "")
    text = payload.get("text", "")
    session_id = payload.get("session_id")
    is_new_session = bool(payload.get("is_new_session", False))
    session_scope = payload.get("session_scope", "topic")
    adapter = payload.get("adapter", "claude-code")
    subagent = payload.get("subagent")
    model = payload.get("model")
    system_prompt = payload.get("system_prompt")
    attachments = payload.get("attachments", [])
    master_url = payload.get("master_url", "http://master:8080")

    if not text:
        LOGGER.warning("agent.empty_prompt topic_id=%s", topic_id)
        return

    client.publish(_status_topic(workspace_id, topic_id), json.dumps({"state": "thinking"}), qos=0)
    LOGGER.info("agent.llm_start topic_id=%s adapter=%s worktree=%s", topic_id, adapter, worktree)

    try:
        if worktree and branch and repo_dir:
            _ensure_worktree(repo_dir, worktree, branch, repo_ref, base_sha)
    except Exception:
        LOGGER.exception("agent.worktree_create_failed worktree=%s branch=%s", worktree, branch)

    cwd = worktree if (worktree and Path(worktree).exists()) else repo_dir or "/"

    # Fetch attachments into the working directory
    for att in attachments:
        try:
            _fetch_attachment(master_url, att["id"], att["filename"], cwd)
        except Exception:
            LOGGER.exception("agent.attachment_fetch_failed id=%s", att.get("id"))

    # Prepend attachment note to prompt text
    if attachments:
        note_lines = "\n".join(
            f'[Attached file: {a["filename"]} — available in the current directory]'
            for a in attachments
        )
        text = f"{note_lines}\n{text}"

    if adapter == "codex":
        response_text, new_session_id, transcript = _run_codex(
            client, workspace_id, topic_id, reply_message_id, agent_name,
            cwd, text, model,
        )
    else:
        # Claude sessions are scoped to the CWD (project directory).
        # For workspace/global scope we use a stable shared directory so
        # --resume works across different topic worktrees.
        session_cwd = cwd
        if session_scope == "workspace":
            session_cwd = f"/workspace/sessions/{workspace_id}"
            Path(session_cwd).mkdir(parents=True, exist_ok=True)
        elif session_scope == "global":
            session_cwd = "/workspace/sessions/global"
            Path(session_cwd).mkdir(parents=True, exist_ok=True)
        response_text, new_session_id, transcript = _run_claude(
            client, workspace_id, topic_id, reply_message_id, agent_name,
            session_cwd, text, session_id, is_new_session, subagent, model, system_prompt,
        )

    LOGGER.info("agent.llm_done topic_id=%s chars=%d", topic_id, len(response_text))

    client.publish(
        _response_topic(workspace_id, topic_id),
        json.dumps({
            "message_id": reply_message_id,
            "agent_name": agent_name,
            "reply_to": message_id,
            "last_response": response_text,
            "transcript": transcript,
            "session_id": new_session_id,
        }),
        qos=1,
    )
    client.publish(_status_topic(workspace_id, topic_id), json.dumps({"state": "idle"}), qos=0)


def _on_connect(client: mqtt.Client, userdata, flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    if reason_code.is_failure:
        LOGGER.error("agent.mqtt_connect_failed reason=%s", reason_code)
        return
    workspace_id = userdata["workspace_id"]
    prompt_sub = f"codex-slack/workspace/{workspace_id}/topic/+/prompt"
    cancel_sub = f"codex-slack/workspace/{workspace_id}/topic/+/cancel"
    client.subscribe(prompt_sub, qos=1)
    client.subscribe(cancel_sub, qos=1)
    LOGGER.info("agent.mqtt_connected subscribed=%s,%s", prompt_sub, cancel_sub)


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    LOGGER.warning("agent.mqtt_disconnected reason=%s", reason_code)


def _on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    LOGGER.info("agent.mqtt_message topic=%s bytes=%d", msg.topic, len(msg.payload))

    parsed_cancel = _parse_cancel_topic(msg.topic)
    if parsed_cancel is not None:
        _, topic_id = parsed_cancel
        try:
            payload = json.loads(msg.payload)
            cancel_msg_id = payload.get("message_id")
        except Exception:
            cancel_msg_id = None
        if cancel_msg_id:
            with _active_procs_lock:
                proc = _active_procs.get(cancel_msg_id)
            if proc is not None:
                LOGGER.info("agent.cancel message_id=%s pid=%s", cancel_msg_id, proc.pid)
                try:
                    proc.kill()
                except Exception:
                    LOGGER.exception("agent.cancel_kill_failed message_id=%s", cancel_msg_id)
            else:
                LOGGER.info("agent.cancel_noop message_id=%s reason=not_found", cancel_msg_id)
        return

    parsed = _parse_prompt_topic(msg.topic)
    if parsed is None:
        return
    workspace_id, topic_id = parsed
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, ValueError):
        LOGGER.warning("agent.mqtt_parse_error topic=%s", msg.topic)
        return
    repo_dir = userdata.get("repo_dir", "")
    master_url = userdata.get("master_url", "http://master:8080")
    # inject master_url into payload so _process_prompt can use it
    payload.setdefault("master_url", master_url)
    _executor.submit(_process_prompt, client, workspace_id, topic_id, payload, repo_dir)


def run_mqtt_loop(
    workspace_id: str,
    mqtt_host: str,
    mqtt_port: int,
    repo_dir: str = "",
    master_url: str = "http://master:8080",
) -> None:
    userdata = {"workspace_id": workspace_id, "repo_dir": repo_dir, "master_url": master_url}
    # Fixed client_id + clean_session=False: Mosquitto queues QoS-1 messages while
    # the agent is stopped and delivers them on reconnect, no matter how long boot takes.
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"agent-{workspace_id}",
        clean_session=False,
        userdata=userdata,
    )
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect(mqtt_host, mqtt_port, keepalive=60)
    LOGGER.info("agent.mqtt_loop_start workspace_id=%s host=%s port=%s", workspace_id, mqtt_host, mqtt_port)
    client.loop_forever()
