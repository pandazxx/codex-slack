from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_mqtt_client(monkeypatch):
    """Prevent the real paho-mqtt thread from starting during master tests.

    Without this, every TestClient(app) startup spawns a paho-mqtt network
    thread that tries to connect to mosquitto:1883 (absent in CI).  paho-mqtt's
    _reconnect_wait() sleeps in 1-second chunks and loop_stop() cannot interrupt
    that sleep — it just sets a flag and blocks on thread.join().  The result is
    ~1 s of real-clock teardown per test, adding 100+ seconds to the suite.

    Tests that need to assert on publish() calls already patch build_mqtt_client
    themselves inside the test body; this fixture's mock is overridden by those
    inner patches for the duration of the with-block, so those assertions are
    unaffected.
    """
    mock = MagicMock()
    monkeypatch.setattr("src.master.main.build_mqtt_client", lambda *a, **kw: mock)
    return mock
