from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from . import notify
from .config import MasterSettings

LOGGER = logging.getLogger(__name__)

_RESPONSE_TOPIC = "codex-slack/workspace/+/topic/+/response"
_STATUS_TOPIC = "codex-slack/workspace/+/topic/+/status"
_CHUNK_TOPIC = "codex-slack/workspace/+/topic/+/chunk"

# Topic pattern: codex-slack/workspace/{wid}/topic/{tid}/{type}
_TOPIC_PARTS = 6


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_mqtt_topic(raw: str) -> tuple[str, str] | None:
    """Return (topic_id, msg_type) from an MQTT topic string, or None if malformed."""
    parts = raw.split("/")
    if len(parts) != _TOPIC_PARTS:
        return None
    return parts[4], parts[5]


def _extract_usage(transcript: str | None) -> str | None:
    """Return serialised usage dict from a stream-json transcript, or None."""
    if not transcript:
        return None
    try:
        events = json.loads(transcript)
        for evt in events:
            if isinstance(evt, dict) and evt.get("type") == "result" and evt.get("usage"):
                return json.dumps(evt["usage"])
    except Exception:
        pass
    return None


def _record_agent_response(db_path: str, topic_id: str) -> None:
    """Set last_responded_at on the workspace that owns this topic.

    Called only on agent `response` events (not status). The idle-stop logic
    compares last_responded_at against last_dispatched_at: the container is
    only eligible for idle-stop when the agent has responded to every dispatch.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE workspaces SET last_responded_at = ?"
                " WHERE id = (SELECT workspace_id FROM topics WHERE id = ?)",
                (_now(), topic_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("mqtt.record_agent_response_error topic_id=%s", topic_id)


def _save_chunk(db_path: str, topic_id: str, payload: dict) -> None:  # type: ignore[type-arg]
    try:
        conn = sqlite3.connect(db_path)
        try:
            message_id = payload["message_id"]
            seq = payload["seq"]
            event = payload["event"]
            agent_name = payload.get("agent_name")
            is_retry = (
                isinstance(event, dict)
                and event.get("type") == "system"
                and event.get("subtype") == "retry"
            )
            with conn:
                if is_retry:
                    conn.execute(
                        "DELETE FROM chunks WHERE message_id = ?", (message_id,)
                    )
                conn.execute(
                    "INSERT INTO chunks (message_id, topic_id, seq, event, agent_name)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (message_id, topic_id, seq, json.dumps(event), agent_name),
                )
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("mqtt.save_chunk_error topic_id=%s", topic_id)


def _save_agent_response(db_path: str, topic_id: str, payload: dict) -> None:  # type: ignore[type-arg]
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            text = payload.get("last_response", "")
            transcript = payload.get("transcript")
            llm_session_id = payload.get("session_id")
            agent_name = payload.get("agent_name")
            message_id = payload.get("message_id") or str(uuid.uuid4())
            usage_json = _extract_usage(transcript)
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO messages"
                    " (id, topic_id, sender, agent_name, text, transcript, usage_json, attachments_json, created_at)"
                    " VALUES (?, ?, 'agent', ?, ?, ?, ?, NULL, ?)",
                    (message_id, topic_id, agent_name, text, transcript, usage_json, _now()),
                )
                conn.execute(
                    "DELETE FROM chunks WHERE message_id = ?", (message_id,)
                )
                if llm_session_id and agent_name:
                    conn.execute(
                        "UPDATE sessions SET llm_session_id = ?, updated_at = ?"
                        " WHERE topic_id = ? AND agent_name = ?",
                        (llm_session_id, _now(), topic_id, agent_name),
                    )
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("mqtt.save_agent_response_error topic_id=%s", topic_id)


def _get_workspace_id(db_path: str, topic_id: str) -> str | None:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT workspace_id FROM topics WHERE id = ?", (topic_id,)).fetchone()
            return row["workspace_id"] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _get_topic_name(db_path: str, topic_id: str) -> str:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT subject FROM topics WHERE id = ?", (topic_id,)).fetchone()
            return row["subject"] if row else ""
        finally:
            conn.close()
    except Exception:
        return ""


def _on_connect(client: mqtt.Client, userdata, flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    if reason_code.is_failure:
        LOGGER.error("mqtt.connect_failed reason=%s", reason_code)
        return
    LOGGER.info("mqtt.connected")
    client.subscribe(_RESPONSE_TOPIC, qos=1)
    client.subscribe(_STATUS_TOPIC, qos=0)
    client.subscribe(_CHUNK_TOPIC, qos=0)
    LOGGER.info("mqtt.subscribed topics=%s,%s,%s", _RESPONSE_TOPIC, _STATUS_TOPIC, _CHUNK_TOPIC)


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    LOGGER.warning("mqtt.disconnected reason=%s", reason_code)


def _on_message(client, userdata, msg: mqtt.MQTTMessage) -> None:
    LOGGER.info("mqtt.message topic=%s payload_bytes=%d", msg.topic, len(msg.payload))

    parsed = _parse_mqtt_topic(msg.topic)
    if parsed is None:
        return
    topic_id, msg_type = parsed

    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, ValueError):
        LOGGER.warning("mqtt.message_parse_error topic=%s", msg.topic)
        return

    db_path = userdata.get("db_path")
    if msg_type == "chunk":
        if db_path:
            _save_chunk(db_path, topic_id, payload)
        if payload.get("seq") == 0:
            LOGGER.info("ws.first_chunk topic_id=%s message_id=%s", topic_id, payload.get("message_id"))
        message = {"type": "chunk", **payload}
    elif msg_type == "status":
        state = payload.get("state")
        message = {"type": "status", "state": state}
        if db_path and state and topic_id:
            try:
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        "UPDATE workspaces SET last_agent_state = ?"
                        " WHERE id = (SELECT workspace_id FROM topics WHERE id = ?)",
                        (state, topic_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                LOGGER.warning("mqtt.persist_agent_state_failed topic_id=%s", topic_id)
    elif msg_type == "response":
        message = {"type": "message", "sender": "agent", **payload}
        if db_path:
            _save_agent_response(db_path, topic_id, payload)
            _record_agent_response(db_path, topic_id)
            notify.notify_reply(
                db_path=db_path,
                settings=userdata["settings"],
                topic_id=topic_id,
                payload=payload,
            )
            app_state = userdata.get("app_state")
            # emit_event self-guards if event infrastructure isn't up; the only thing
            # we need to check here is that we have the app_state to pass through.
            if app_state is not None:
                workspace_id = _get_workspace_id(db_path, topic_id)
                topic_name = _get_topic_name(db_path, topic_id)
                if workspace_id:
                    from .event_dispatcher import emit_event
                    emit_event(
                        app_state=app_state,
                        event_type="topic_message_received",
                        topic_id=topic_id,
                        workspace_id=workspace_id,
                        timing="after",
                        variables={
                            "msgbody": payload.get("last_response", ""),
                            "topic_name": topic_name,
                        },
                    )
    else:
        return

    hub = userdata.get("hub")
    loop = userdata.get("loop")
    if hub is not None and loop is not None:
        hub.broadcast_threadsafe("_global", {"topic_id": topic_id, **message}, loop)


def build_client(
    settings: MasterSettings,
    hub=None,
    loop: asyncio.AbstractEventLoop | None = None,
    db_path: str | None = None,
    app_state=None,
) -> mqtt.Client:
    userdata = {"hub": hub, "loop": loop, "db_path": db_path, "settings": settings, "app_state": app_state}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata=userdata)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    return client
