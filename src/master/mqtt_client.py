from __future__ import annotations

import logging

import paho.mqtt.client as mqtt

from .config import MasterSettings

LOGGER = logging.getLogger(__name__)

_RESPONSE_TOPIC = "codex-slack/workspace/+/topic/+/response"
_STATUS_TOPIC = "codex-slack/workspace/+/topic/+/status"


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


def build_client(settings: MasterSettings) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    return client
