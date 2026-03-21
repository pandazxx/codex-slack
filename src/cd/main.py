from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from .config import load_cd_settings
from .daemon import run_loop


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.StreamHandler()],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    load_dotenv()
    # If CD_ENV_FILE points to a specific env file (e.g. .env.staging), load it
    # into the daemon's own process so daemon-level vars like CD_NOTIFY_* are visible.
    env_file = os.getenv("CD_ENV_FILE", "").strip()
    if env_file:
        load_dotenv(env_file, override=True)
    configure_logging()
    settings = load_cd_settings()
    run_loop(settings)


if __name__ == "__main__":
    main()
