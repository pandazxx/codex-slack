from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ..logging_utils import LocalTimeFormatter
from .config import load_master_settings
from .db import init_db, schema_info
from .messages import router as messages_router
from .mqtt_client import build_client as build_mqtt_client
from .topics import router as topics_router
from .workspaces import router as workspaces_router
from .ws_hub import ConnectionHub

LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _db_path(data_dir: str) -> str:
    return str(Path(data_dir) / "master_data.db")


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    load_dotenv()
    configure_logging()
    settings = load_master_settings()
    LOGGER.info(
        "master.startup mqtt=%s:%s data_dir=%s dry_run=%s base_image=%s runtime=%s",
        settings.mqtt_host,
        settings.mqtt_port,
        settings.data_dir,
        settings.dry_run,
        settings.agent_base_image,
        settings.container_runtime,
    )
    db_path = _db_path(settings.data_dir)
    init_db(db_path)
    LOGGER.info("master.db_init path=%s", db_path)
    hub = ConnectionHub()
    loop = asyncio.get_event_loop()
    mqtt = build_mqtt_client(settings, hub=hub, loop=loop, db_path=db_path)
    mqtt.loop_start()
    LOGGER.info("master.mqtt_loop_start host=%s port=%s", settings.mqtt_host, settings.mqtt_port)
    app.state.settings = settings
    app.state.db_path = db_path
    app.state.hub = hub
    app.state.mqtt = mqtt
    yield
    mqtt.loop_stop()
    mqtt.disconnect()
    LOGGER.info("master.shutdown")


app = FastAPI(lifespan=lifespan)
app.include_router(workspaces_router)
app.include_router(topics_router)
app.include_router(messages_router)


@app.get("/health")
async def health() -> dict:  # type: ignore[type-arg]
    return {"status": "ok"}


@app.get("/schema")
async def schema() -> dict:  # type: ignore[type-arg]
    return schema_info(app.state.db_path)


@app.websocket("/ws/{topic_id}")
async def ws_endpoint(topic_id: str, websocket: WebSocket) -> None:
    await websocket.accept()
    app.state.hub.connect(topic_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        app.state.hub.disconnect(topic_id, websocket)
