from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

LOGGER = logging.getLogger(__name__)


class ConnectionHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    def connect(self, topic_id: str, ws: WebSocket) -> None:
        self._connections[topic_id].add(ws)
        LOGGER.info("ws.connect topic_id=%s total=%d", topic_id, len(self._connections[topic_id]))

    def disconnect(self, topic_id: str, ws: WebSocket) -> None:
        self._connections[topic_id].discard(ws)
        LOGGER.info("ws.disconnect topic_id=%s remaining=%d", topic_id, len(self._connections[topic_id]))

    async def broadcast(self, topic_id: str, message: dict) -> None:  # type: ignore[type-arg]
        dead: list[WebSocket] = []
        for ws in list(self._connections.get(topic_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[topic_id].discard(ws)

    def broadcast_threadsafe(self, topic_id: str, message: dict, loop: asyncio.AbstractEventLoop) -> None:  # type: ignore[type-arg]
        asyncio.run_coroutine_threadsafe(self.broadcast(topic_id, message), loop)
