from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from .config import MasterSettings

LOGGER = logging.getLogger(__name__)

_RESPONSE_TOPIC = "codex-slack/workspace/+/topic/+/response"
_STATUS_TOPIC = "codex-slack/workspace/+/topic/+/status"

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
            conn.execute(
                "INSERT OR IGNORE INTO messages"
                " (id, topic_id, sender, agent_name, text, transcript, usage_json, attachments_json, created_at)"
                " VALUES (?, ?, 'agent', ?, ?, ?, ?, NULL, ?)",
                (message_id, topic_id, agent_name, text, transcript, usage_json, _now()),
            )
            if llm_session_id and agent_name:
                conn.execute(
                    "UPDATE sessions SET llm_session_id = ?, updated_at = ?"
                    " WHERE topic_id = ? AND agent_name = ?",
                    (llm_session_id, _now(), topic_id, agent_name),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("mqtt.save_agent_response_error topic_id=%s", topic_id)


def _on_connect(client: mqtt.Client, userdata, flags, reason_code, properties) -> None:  # type: ignore[type-arg]
    if reason_code.is_failure:
        LOGGER.error("mqtt.connect_failed reason=%s", reason_code)
        return
    LOGGER.info("mqtt.connected")
    client.subscribe(_RESPONSE_TOPIC, qos=1)
    client.subscribe(_STATUS_TOPIC, qos=0)
    LOGGER.info("mqtt.subscribed topics=%s,%s", _RESPONSE_TOPIC, _STATUS_TOPIC)


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

    if msg_type == "status":
        message = {"type": "status", "state": payload.get("state")}
    elif msg_type == "response":
        message = {"type": "message", "sender": "agent", **payload}
        db_path = userdata.get("db_path")
        if db_path:
            _save_agent_response(db_path, topic_id, payload)
    else:
        return

    hub = userdata.get("hub")
    loop = userdata.get("loop")
    if hub is not None and loop is not None:
        hub.broadcast_threadsafe(topic_id, message, loop)


def build_client(
    settings: MasterSettings,
    hub=None,
    loop: asyncio.AbstractEventLoop | None = None,
    db_path: str | None = None,
) -> mqtt.Client:
    userdata = {"hub": hub, "loop": loop, "db_path": db_path}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata=userdata)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    return client
