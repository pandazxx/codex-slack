from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..logging_utils import LocalTimeFormatter
from .agent_runner import container_name, get_container_status, pause_agent, refresh_auth, spawn_agent, start_agent_if_stopped, stop_agent
from .config import load_master_settings
from .db import get_connection, init_db, schema_info
from .runtime_config import load_agent_env, load_global_env
from .attachments import router as attachments_router
from .messages import router as messages_router
from .mqtt_client import build_client as build_mqtt_client
from .runtime_config import global_router as global_config_router
from .runtime_config import workspace_router as workspace_config_router
from .staffs import global_router as global_staffs_router
from .staffs import topic_router as topic_staffs_router
from .staffs import workspace_router as workspace_staffs_router
from .storage import LocalAttachmentStore
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


def _active_workspaces(db_path: str) -> list[dict]:  # type: ignore[type-arg]
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, container_name, last_message_at, last_refreshed_at"
            " FROM workspaces WHERE archived_at IS NULL AND container_name IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _background_tasks(settings, db_path: str, stop_event: threading.Event) -> None:
    """Background loop: idle auto-stop, health-check respawn, and auth auto-refresh."""
    idle_timeout = settings.agent_idle_timeout_seconds
    auth_interval = settings.agent_auth_refresh_interval_seconds

    while not stop_event.wait(60):
        now = time.time()
        try:
            workspaces = _active_workspaces(db_path)
            global_cfg = load_global_env(db_path)
        except Exception:
            LOGGER.exception("master.bg_task_list_failed")
            continue
        gh_token_effective = settings.gh_token or global_cfg.get("GH_TOKEN")

        for ws in workspaces:
            cname = ws["container_name"]
            ws_id = ws["id"]

            # Health check: restart containers that exited unexpectedly
            try:
                status = get_container_status(name=cname, dry_run=settings.dry_run)
                exit_code = status.get("exit_code") or 0
                # 143 = SIGTERM: container was gracefully stopped by pause_agent (idle-stop).
                # Do not restart — let auto-start on next message handle it.
                if status["status"] == "exited" and exit_code not in (0, 143):
                    LOGGER.warning("master.health_restart container=%s exit_code=%s", cname, exit_code)
                    start_agent_if_stopped(name=cname, dry_run=settings.dry_run)
            except Exception:
                LOGGER.exception("master.health_check_failed container=%s", cname)

            # Idle auto-stop
            if idle_timeout > 0:
                last_msg = ws.get("last_message_at")
                if last_msg:
                    try:
                        import datetime
                        last_ts = datetime.datetime.strptime(last_msg, "%Y-%m-%dT%H:%M:%SZ").timestamp()
                        if now - last_ts > idle_timeout:
                            st = get_container_status(name=cname, dry_run=settings.dry_run)
                            if st["status"] == "running":
                                LOGGER.info("master.idle_stop container=%s idle_s=%d", cname, int(now - last_ts))
                                pause_agent(name=cname, dry_run=settings.dry_run)
                    except Exception:
                        LOGGER.exception("master.idle_stop_failed container=%s", cname)

            # Auto auth-refresh
            if auth_interval > 0:
                last_ref = ws.get("last_refreshed_at")
                needs_refresh = last_ref is None
                if not needs_refresh and last_ref:
                    try:
                        import datetime
                        ref_ts = datetime.datetime.strptime(last_ref, "%Y-%m-%dT%H:%M:%SZ").timestamp()
                        needs_refresh = now - ref_ts > auth_interval
                    except Exception:
                        pass
                if needs_refresh:
                    try:
                        LOGGER.info("master.auto_refresh_auth container=%s", cname)
                        refresh_auth(name=cname, gh_token=gh_token_effective, dry_run=settings.dry_run)
                        from datetime import datetime, timezone
                        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        conn = get_connection(db_path)
                        try:
                            conn.execute(
                                "UPDATE workspaces SET last_refreshed_at = ? WHERE id = ?",
                                (now_str, ws_id),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    except Exception:
                        LOGGER.exception("master.auto_refresh_auth_failed container=%s", cname)


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
            "SELECT id, repo_url, container_name FROM workspaces"
            " WHERE container_name IS NOT NULL AND archived_at IS NULL"
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
                master_url=settings.master_url,
                extra_env=load_agent_env(db_path, ws_id),
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
    attachment_dir = settings.effective_attachment_data_dir()
    attachment_store = LocalAttachmentStore(attachment_dir)
    LOGGER.info("master.attachment_store dir=%s", attachment_dir)
    app.state.settings = settings
    app.state.db_path = db_path
    app.state.hub = hub
    app.state.mqtt = mqtt
    app.state.attachment_store = attachment_store
    _respawn_agents(settings, db_path)

    stop_event = threading.Event()
    bg_thread = threading.Thread(
        target=_background_tasks, args=(settings, db_path, stop_event), daemon=True, name="master-bg"
    )
    bg_thread.start()
    LOGGER.info("master.bg_task_start idle_timeout=%ds auth_refresh=%ds",
                settings.agent_idle_timeout_seconds, settings.agent_auth_refresh_interval_seconds)

    yield

    stop_event.set()
    bg_thread.join(timeout=5)
    mqtt.loop_stop()
    mqtt.disconnect()
    LOGGER.info("master.shutdown")


app = FastAPI(lifespan=lifespan)
app.include_router(workspaces_router, prefix="/api")
app.include_router(topics_router, prefix="/api")
app.include_router(messages_router, prefix="/api")
app.include_router(global_staffs_router, prefix="/api")
app.include_router(workspace_staffs_router, prefix="/api")
app.include_router(topic_staffs_router, prefix="/api")
app.include_router(global_config_router, prefix="/api")
app.include_router(workspace_config_router, prefix="/api")
app.include_router(attachments_router, prefix="/api")

if (_STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="static-assets")


@app.get("/health")
async def health() -> dict:  # type: ignore[type-arg]
    return {"status": "ok"}


@app.get("/schema")
async def schema() -> dict:  # type: ignore[type-arg]
    return schema_info(app.state.db_path)


async def _replay_in_progress_chunks(ws: WebSocket, topic_id: str, db_path: str) -> None:
    def _query() -> list[tuple[str, list[dict]]]:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            in_progress = [
                r["message_id"] for r in conn.execute(
                    "SELECT DISTINCT c.message_id FROM chunks c"
                    " LEFT JOIN messages m ON m.id = c.message_id"
                    " WHERE c.topic_id = ? AND m.id IS NULL",
                    (topic_id,),
                ).fetchall()
            ]
            result = []
            for mid in in_progress:
                rows = conn.execute(
                    "SELECT event FROM chunks WHERE message_id = ? ORDER BY seq",
                    (mid,),
                ).fetchall()
                result.append((mid, [json.loads(r["event"]) for r in rows]))
            return result
        finally:
            conn.close()

    streams = await asyncio.get_running_loop().run_in_executor(None, _query)
    for message_id, events in streams:
        if events:
            await ws.send_json({
                "type": "chunk_replay",
                "message_id": message_id,
                "events": events,
            })
    LOGGER.info("ws.chunk_replay topic_id=%s streams=%d", topic_id, len(streams))


@app.websocket("/ws/{topic_id}")
async def ws_endpoint(topic_id: str, websocket: WebSocket) -> None:
    await websocket.accept()
    app.state.hub.connect(topic_id, websocket)
    try:
        await _replay_in_progress_chunks(websocket, topic_id, app.state.db_path)
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
