from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from ..logging_utils import LocalTimeFormatter
from .config import load_master_settings
from .db import init_db, schema_info
from .topics import router as topics_router
from .workspaces import router as workspaces_router

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
    app.state.settings = settings
    app.state.db_path = db_path
    yield
    LOGGER.info("master.shutdown")


app = FastAPI(lifespan=lifespan)
app.include_router(workspaces_router)
app.include_router(topics_router)


@app.get("/health")
async def health() -> dict:  # type: ignore[type-arg]
    return {"status": "ok"}


@app.get("/schema")
async def schema() -> dict:  # type: ignore[type-arg]
    return schema_info(app.state.db_path)
