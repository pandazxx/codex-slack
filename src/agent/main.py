from __future__ import annotations

import argparse
import logging
import signal

from .worker import load_worker_settings, run_worker
from ..logging_utils import LocalTimeFormatter
from ..version import get_app_version

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent worker runtime")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args()


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler])


# Pre-flight diagnostic: log receipt of any termination signal so post-mortem can
# distinguish "got SIGTERM, escalated to SIGKILL by tini" from "direct SIGKILL".
# The MQTT loop installs a fuller SIGTERM handler later that also publishes
# interrupt events; this is replaced in-place when it does.
def _diagnostic_signal_handler(signum: int, frame) -> None:
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = str(signum)
    LOGGER.warning("agent.signal_received signum=%d name=%s phase=pre-mqtt-loop", signum, name)
    raise SystemExit(128 + signum)


def install_diagnostic_signal_handlers() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        try:
            signal.signal(sig, _diagnostic_signal_handler)
        except (ValueError, OSError):
            pass


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    install_diagnostic_signal_handlers()
    LOGGER.info("agent.startup version=%s", get_app_version())
    settings = load_worker_settings()
    return run_worker(settings)


if __name__ == "__main__":
    raise SystemExit(main())
