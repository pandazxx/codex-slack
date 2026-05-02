from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from ..logging_utils import LocalTimeFormatter
from .config import load_master_settings


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    load_dotenv()
    configure_logging()
    settings = load_master_settings()
    logging.getLogger(__name__).info(
        "master.startup mqtt=%s:%s data_dir=%s dry_run=%s base_image=%s",
        settings.mqtt_host,
        settings.mqtt_port,
        settings.data_dir,
        settings.dry_run,
        settings.agent_base_image,
    )
    yield
    logging.getLogger(__name__).info("master.shutdown")


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict:  # type: ignore[type-arg]
    return {"status": "ok"}
