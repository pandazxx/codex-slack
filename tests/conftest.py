from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Ensure a current asyncio event loop exists for every test.

    Python 3.10+ no longer auto-creates an event loop on get_event_loop().
    asyncio.run() (used in test_ws_hub) closes and clears the loop after each
    call, which breaks any subsequent test that calls asyncio.get_event_loop()
    directly (e.g. the _run helper in test_master_replay).  This fixture
    creates a fresh loop and sets it as the current loop before each test,
    then closes it after.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()
    asyncio.set_event_loop(None)
