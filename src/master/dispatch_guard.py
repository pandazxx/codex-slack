from __future__ import annotations

import logging
import signal
import sys
import threading

LOGGER = logging.getLogger(__name__)

_lock = threading.Lock()
_count = 0
_zero_event = threading.Event()
_zero_event.set()  # starts at zero
_shutting_down = False


def is_shutting_down() -> bool:
    """Return True once a SIGTERM has been received."""
    return _shutting_down


class _DispatchGuard:
    """Sync context manager that increments the in-flight counter for one dispatch."""

    def __enter__(self) -> "_DispatchGuard":
        global _count
        with _lock:
            _count += 1
            _zero_event.clear()
        return self

    def __exit__(self, *_: object) -> None:
        global _count
        with _lock:
            _count -= 1
            if _count == 0:
                _zero_event.set()


def in_flight_dispatch() -> _DispatchGuard:
    """Return a context manager that tracks one in-flight dispatch.

    Usage (sync)::

        with in_flight_dispatch():
            response = router.route_prompt(...)

    Usage (async — works because _DispatchGuard is a plain sync CM)::

        async with channel.typing():
            with in_flight_dispatch():
                response = await asyncio.to_thread(router.route_prompt, ...)
    """
    return _DispatchGuard()


def install_sigterm_handler(drain_timeout_seconds: int = 300) -> None:
    """Install a SIGTERM handler that drains in-flight dispatches before exiting.

    On SIGTERM:
    1. Sets the ``shutting_down`` flag so new requests are dropped at the handler level.
    2. Waits up to *drain_timeout_seconds* for all in-flight dispatches to finish.
    3. Calls ``sys.exit(0)`` (cleanly) regardless — SIGKILL from compose/podman is the backstop.
    """

    def _handler(signum: int, frame: object) -> None:
        global _shutting_down
        with _lock:
            current = _count
        LOGGER.info(
            "master.sigterm_received in_flight=%d drain_timeout_seconds=%d",
            current,
            drain_timeout_seconds,
        )
        _shutting_down = True
        drained = _zero_event.wait(timeout=float(drain_timeout_seconds))
        if drained:
            LOGGER.info("master.drain_complete exiting cleanly")
        else:
            with _lock:
                remaining = _count
            LOGGER.warning(
                "master.drain_timeout in_flight=%d forcing exit",
                remaining,
            )
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handler)
    LOGGER.info("master.sigterm_handler_installed drain_timeout_seconds=%d", drain_timeout_seconds)
