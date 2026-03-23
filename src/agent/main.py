from __future__ import annotations

import argparse
import logging

from .worker import load_worker_settings, run_worker
from ..logging_utils import LocalTimeFormatter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent worker runtime")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args()


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler])


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    settings = load_worker_settings()
    return run_worker(settings)


if __name__ == "__main__":
    raise SystemExit(main())
