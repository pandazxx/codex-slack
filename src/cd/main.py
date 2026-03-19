from __future__ import annotations

import logging

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
    configure_logging()
    settings = load_cd_settings()
    run_loop(settings)


if __name__ == "__main__":
    main()
