"""MQTT-driven prompt processor for the v3 agent worker."""
from __future__ import annotations

import json
import logging
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

LOGGER = logging.getLogger(__name__)

_TOPIC_PARTS = 6  # codex-slack/workspace/{wid}/topic/{tid}/prompt
_LLM_TIMEOUT = 300

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-llm")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_prompt_topic(raw: str) -> tuple[str, str] | None:
    parts = raw.split("/")
    if len(parts) != _TOPIC_PARTS or parts[5] != "prompt":
        return None
    return parts[2], parts[4]  # workspace_id, topic_id


def _status_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/status"


def _response_topic(workspace_id: str, topic_id: str) -> str:
    return f"codex-slack/workspace/{workspace_id}/topic/{topic_id}/response"


def _ensure_worktree(repo_dir: str, worktree_path: str, branch: str) -> None:
    if Path(worktree_path).exists():
        return
    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", worktree_path, "-b", branch],
            capture_output=True, text=True, check=True,
        )
        LOGGER.info("agent.worktree_created path=%s branch=%s", worktree_path, branch)
    except subprocess.CalledProcessError:
        # Branch already exists — check out without -b
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", worktree_path, branch],
            capture_output=True, text=True, check=True,
        )
        LOGGER.info("agent.worktree_reused path=%s branch=%s", worktree_path, branch)


def _run_claude(worktree: str, text: str, session_id: str | None, subagent: str | None) -> tuple[str, str | None, str | None]:
    cmd = ["claude", "--print", "--output-format", "json", "--dangerously-skip-permissions"]
    if session_id:
        cmd += ["--resume", session_id]
    cmd.append(text)
    try:
        result = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, timeout=_LLM_TIMEOUT)
        raw = result.stdout.strip()
        try:
            data = json.loads(raw)
            new_session_id = data.get("session_id")
            output = data.get("result") or data.get("last_response") or "(no output)"
            return output, new_session_id, raw
        except (json.JSONDecodeError, AttributeError):
            return raw or result.stderr.strip() or "(no output)", None, None
    except subprocess.TimeoutExpired:
        return f"(claude timed out after {_LLM_TIMEOUT}s)", None, None
    except FileNotFoundError:
        return "(claude CLI not found in agent container)", None, None
    except Exception as exc:
        return f"(claude error: {exc})", None, None


def _run_codex(worktree: str, text: str) -> tuple[str, str | None, str | None]:
    cmd = ["codex", "--full-auto", "-q", text]
    try:
        result = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, timeout=_LLM_TIMEOUT)
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        return output, None, None
    except subprocess.TimeoutExpired:
        return f"(codex timed out after {_LLM_TIMEOUT}s)", None, None
    except FileNotFoundError:
        return "(codex CLI not found in agent container)", None, None
    except Exception as exc:
        return f"(codex error: {exc})", None, None


def _process_prompt(
    client: mqtt.Client,
    workspace_id: str,
    topic_id: str,
    payload: dict,  # type: ignore[type-arg]
    repo_dir: str,
) -> None:
    message_id = payload.get("message_id") or str(uuid.uuid4())
    agent_name = payload.get("agent_name", "claude")
    worktree = payload.get("worktree", "")
    branch = payload.get("branch", "")
    text = payload.get("text", "")
    session_id = payload.get("session_id")
    adapter = payload.get("adapter", "claude-code")
    subagent = payload.get("subagent")

    if not text:
        LOGGER.warning("agent.empty_prompt topic_id=%s", topic_id)
        return

    client.publish(_status_topic(workspace_id, topic_id), json.dumps({"state": "thinking"}), qos=0)
    LOGGER.info("agent.llm_start topic_id=%s adapter=%s worktree=%s", topic_id, adapter, worktree)

    try:
        if worktree and branch and repo_dir:
            _ensure_worktree(repo_dir, worktree, branch)
    except Exception:
        LOGGER.exception("agent.worktree_create_failed worktree=%s branch=%s", worktree, branch)

    cwd = worktree if (worktree and Path(worktree).exists()) else repo_dir or "/"

    if adapter == "codex":
        response_text, new_session_id, transcript = _run_codex(cwd, text)
    else:
        response_text, new_session_id, transcript = _run_claude(cwd, text, session_id, subagent)

    LOGGER.info("agent.llm_done topic_id=%s chars=%d", topic_id, len(response_text))

    client.publish(
        _response_topic(workspace_id, topic_id),
        json.dumps({
            "message_id": str(uuid.uuid4()),
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
    client.subscribe(prompt_sub, qos=1)
    LOGGER.info("agent.mqtt_connected subscribed=%s", prompt_sub)


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    LOGGER.warning("agent.mqtt_disconnected reason=%s", reason_code)


def _on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    LOGGER.info("agent.mqtt_message topic=%s bytes=%d", msg.topic, len(msg.payload))
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
    _executor.submit(_process_prompt, client, workspace_id, topic_id, payload, repo_dir)


def run_mqtt_loop(workspace_id: str, mqtt_host: str, mqtt_port: int, repo_dir: str = "") -> None:
    userdata = {"workspace_id": workspace_id, "repo_dir": repo_dir}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata=userdata)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect(mqtt_host, mqtt_port, keepalive=60)
    LOGGER.info("agent.mqtt_loop_start workspace_id=%s host=%s port=%s", workspace_id, mqtt_host, mqtt_port)
    client.loop_forever()
