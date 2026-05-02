from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..logging_utils import LocalTimeFormatter
from .agent_runner import container_name, spawn_agent, stop_agent
from .config import load_master_settings
from .db import get_connection, init_db, schema_info
from .messages import router as messages_router
from .mqtt_client import build_client as build_mqtt_client
from .topics import router as topics_router
from .workspaces import router as workspaces_router
from .ws_hub import ConnectionHub

LOGGER = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _db_path(data_dir: str) -> str:
    return str(Path(data_dir) / "master_data.db")


def _respawn_agents(settings, db_path: str) -> None:
    """Respawn agent containers for all workspaces on master startup."""
    import docker
    import docker.errors
    try:
        docker_client = docker.from_env()
    except Exception:
        LOGGER.warning("master.respawn_skip reason=docker_unavailable")
        return

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, repo_url, container_name FROM workspaces WHERE container_name IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        ws_id = row["id"]
        cname = row["container_name"]
        try:
            docker_client.containers.get(cname)
            LOGGER.info("master.respawn_skip container=%s reason=already_running", cname)
            continue
        except docker.errors.NotFound:
            pass
        try:
            spawn_agent(
                runtime=settings.container_runtime,
                workspace_id=ws_id,
                repo_url=row["repo_url"],
                image=settings.agent_base_image,
                mqtt_host=settings.mqtt_host,
                mqtt_port=settings.mqtt_port,
                network=settings.agent_network,
                claude_code_oauth_token=settings.claude_code_oauth_token,
                anthropic_api_key=settings.anthropic_api_key,
                openai_api_key=settings.openai_api_key,
                gh_token=settings.gh_token,
                ssh_auth_sock_path=settings.agent_ssh_auth_sock_path,
                ssh_known_hosts_path=settings.agent_ssh_known_hosts_path,
                dry_run=settings.dry_run,
            )
            LOGGER.info("master.respawned container=%s workspace_id=%s", cname, ws_id)
        except Exception:
            LOGGER.exception("master.respawn_failed workspace_id=%s", ws_id)


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
    _respawn_agents(settings, db_path)
    yield
    mqtt.loop_stop()
    mqtt.disconnect()
    LOGGER.info("master.shutdown")


app = FastAPI(lifespan=lifespan)
app.include_router(workspaces_router, prefix="/api")
app.include_router(topics_router, prefix="/api")
app.include_router(messages_router, prefix="/api")

if (_STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="static-assets")


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


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_index(full_path: str) -> FileResponse:
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "frontend not built")
    return FileResponse(str(index))
