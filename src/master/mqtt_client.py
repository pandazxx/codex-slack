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
_VERDICT_TOPIC = "codex-slack/workspace/+/topic/+/verdict"
_PONG_TOPIC = "codex-slack/workspace/+/topic/+/pong"

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
            if transcript is None and text == "(message interrupted)":
                chunk_rows = conn.execute(
                    "SELECT event FROM chunks WHERE message_id = ? ORDER BY seq",
                    (message_id,),
                ).fetchall()
                if chunk_rows:
                    events = []
                    for r in chunk_rows:
                        try:
                            events.append(json.loads(r["event"]))
                        except Exception:
                            pass
                    if events:
                        transcript = json.dumps(events)
            usage_json = _extract_usage(transcript)
            with conn:
                # If the stale-stream detector beat us here with "(message interrupted)",
                # update it with the real response instead of silently dropping it.
                conn.execute(
                    "UPDATE messages SET text=?, transcript=?, usage_json=?"
                    " WHERE id=? AND text=?",
                    (text, transcript, usage_json, message_id, "(message interrupted)"),
                )
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


def _prompt_was_event_dispatched(db_path: str, reply_to: str) -> bool:
    """Return True if the prompt message that triggered this reply had sender='event'.

    Agent replies to event-dispatched prompts must not re-fire topic_message_received,
    otherwise every event action on that event type loops indefinitely.
    """
    if not reply_to:
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT sender FROM messages WHERE id = ?", (reply_to,)
            ).fetchone()
            return row is not None and row[0] == "event"
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("mqtt.prompt_sender_check_error reply_to=%s", reply_to)
        return False


def _get_structured_output_action(db_path: str, message_id: str) -> sqlite3.Row | None:
    """Return the event_action row if message_id links to a structured-output action."""
    if not message_id:
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            msg_row = conn.execute(
                "SELECT event_action_id FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if msg_row is None or not msg_row["event_action_id"]:
                return None
            action_row = conn.execute(
                "SELECT * FROM event_actions WHERE id = ? AND structured_output = 1",
                (msg_row["event_action_id"],),
            ).fetchone()
            return action_row
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("mqtt.get_structured_output_action_error message_id=%s", message_id)
        return None


def _discard_chunks(db_path: str, message_id: str) -> None:
    """Delete streaming chunks and broadcast a retract event so the frontend clears the bubble."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DELETE FROM chunks WHERE message_id = ?", (message_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        LOGGER.warning("structured_output.discard_chunks_failed message_id=%s", message_id)


def _resolve_gate_future(app_state, loop, reply_to: str, result: str) -> None:
    """Set the result on a pending gate Future keyed by reply_to, if one exists."""
    if not reply_to or app_state is None or loop is None:
        return
    gate_futures: dict = getattr(app_state, "gate_futures", {})
    fut = gate_futures.get(reply_to)
    if fut is not None and not fut.done():
        loop.call_soon_threadsafe(fut.set_result, result)


def _handle_structured_response(
    db_path: str,
    action: sqlite3.Row,
    topic_id: str,
    payload: dict,  # type: ignore[type-arg]
    userdata: dict,  # type: ignore[type-arg]
) -> None:
    """Parse staff JSON response and act on it for a structured-output action."""
    from .event_dispatcher import _record_run

    response_text = payload.get("last_response", "")
    reply_message_id = payload.get("message_id", "")
    reply_to = payload.get("reply_to", "")
    hub = userdata.get("hub")
    loop = userdata.get("loop")
    app_state = userdata.get("app_state")

    # Discard streaming chunks so the frontend bubble carrying the raw JSON is cleared.
    if reply_message_id:
        _discard_chunks(db_path, reply_message_id)
    if hub is not None and loop is not None and reply_message_id:
        hub.broadcast_threadsafe(
            "_global",
            {"type": "chunk_retract", "topic_id": topic_id, "message_id": reply_message_id},
            loop,
        )

    try:
        response = json.loads(response_text)
    except (json.JSONDecodeError, ValueError):
        LOGGER.warning(
            "structured_output.invalid_json action_id=%s text=%r",
            action["id"], response_text[:200],
        )
        _record_run(db_path, action["id"], status="ok", output=f"invalid_json: {response_text[:200]}")
        _resolve_gate_future(app_state, loop, reply_to, "proceed")
        return

    # Resolve gate future before handling the response shape.
    _resolve_gate_future(app_state, loop, reply_to, "break" if response.get("break") else "proceed")

    if response.get("silent"):
        log_msg = response.get("log", "")
        LOGGER.info("structured_output.silent action_id=%s log=%r", action["id"], log_msg)
        _record_run(db_path, action["id"], status="ok", output=f"silent log={log_msg!r}")
    elif response.get("break"):
        msg_text = response.get("message", "")
        LOGGER.info("structured_output.break action_id=%s message=%r", action["id"], msg_text)
        _record_run(db_path, action["id"], status="ok", output=f"break message={msg_text!r}")
    elif "message" in response:
        msg_text = str(response["message"])
        app_state = userdata.get("app_state")
        if app_state and loop:
            from .dispatch import post_message_direct
            fut = asyncio.run_coroutine_threadsafe(
                post_message_direct(
                    app_state=app_state,
                    topic_id=topic_id,
                    text=msg_text,
                    sender="agent",
                    agent_name=action["staff_name"],
                ),
                loop,
            )
            try:
                new_message_id = fut.result(timeout=5.0)
                _record_run(
                    db_path, action["id"],
                    status="ok",
                    output=f"posted message_id={new_message_id}",
                )
            except Exception as exc:
                LOGGER.exception("structured_output.post_failed action_id=%s", action["id"])
                _record_run(db_path, action["id"], status="dispatch_error", output=str(exc))
        else:
            LOGGER.warning("structured_output.no_app_state action_id=%s", action["id"])
            _record_run(db_path, action["id"], status="dispatch_error", output="no app_state/loop")
    else:
        LOGGER.warning("structured_output.unrecognised action_id=%s response=%r", action["id"], response)
        _record_run(db_path, action["id"], status="ok", output=f"unrecognised_shape: {response_text[:200]}")


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
    client.subscribe(_VERDICT_TOPIC, qos=1)
    client.subscribe(_PONG_TOPIC, qos=0)
    LOGGER.info(
        "mqtt.subscribed topics=%s,%s,%s,%s,%s",
        _RESPONSE_TOPIC, _STATUS_TOPIC, _CHUNK_TOPIC, _VERDICT_TOPIC, _PONG_TOPIC,
    )


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
            # Check before saving — structured-output responses are handled separately
            # and suppressed from the normal agent-message flow.
            # Use reply_to (the prompt's message_id) not message_id (the agent's new UUID).
            action = _get_structured_output_action(db_path, payload.get("reply_to", ""))
            if action is not None:
                _handle_structured_response(db_path, action, topic_id, payload, userdata)
                return

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
            # Skip if this reply is responding to an event-dispatched prompt — otherwise
            # every topic_message_received action loops: action fires → agent replies →
            # topic_message_received re-fires → action fires again.
            reply_to = payload.get("reply_to", "")
            if app_state is not None and not _prompt_was_event_dispatched(db_path, reply_to):
                workspace_id = _get_workspace_id(db_path, topic_id)
                topic_name = _get_topic_name(db_path, topic_id)
                if workspace_id:
                    from .event_dispatcher import emit_event
                    last_response = payload.get("last_response", "")
                    agent_name = payload.get("agent_name")
                    emit_event(
                        app_state=app_state,
                        event_type="topic_message_received",
                        topic_id=topic_id,
                        workspace_id=workspace_id,
                        timing="after",
                        variables={
                            "msgbody": last_response,
                            "topic_name": topic_name,
                            "response_json": json.dumps({
                                "text": last_response,
                                "agent_name": agent_name,
                                "sender": "agent",
                            }),
                        },
                    )
    elif msg_type == "verdict":
        reply_to = payload.get("reply_to")
        if reply_to:
            loop = userdata.get("loop")
            app_state = userdata.get("app_state")
            if loop and app_state:
                def _resolve_verdict(mid=reply_to, data=payload):
                    fut = getattr(app_state, "veto_futures", {}).get(mid)
                    if fut is not None and not fut.done():
                        fut.set_result(data)
                loop.call_soon_threadsafe(_resolve_verdict)
        LOGGER.info(
            "mqtt.verdict topic_id=%s reply_to=%s verdict=%s",
            topic_id,
            reply_to,
            payload.get("verdict"),
        )
        return
    elif msg_type == "pong":
        try:
            app_state = userdata.get("app_state")
            if app_state:
                message_id = payload.get("message_id")
                alive = payload.get("alive", False)
                pending_pings = getattr(app_state, "pending_pings", {})
                pending = pending_pings.get(message_id)
                if pending is not None:
                    pending["alive"] = alive
                    pending["event"].set()
                    LOGGER.info("mqtt.pong_received message_id=%s alive=%s", message_id, alive)
        except Exception:
            LOGGER.exception("mqtt.pong_handler_error")
        return
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
