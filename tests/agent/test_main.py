from __future__ import annotations

import signal
from unittest.mock import patch

import pytest

import src.agent.main as agent_main


@pytest.fixture
def restore_signal_handlers():
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_install_diagnostic_signal_handlers_registers_all_four(restore_signal_handlers):
    agent_main.install_diagnostic_signal_handlers()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        assert signal.getsignal(sig) is agent_main._diagnostic_signal_handler


def test_diagnostic_signal_handler_logs_signal_name_and_exits(restore_signal_handlers, caplog):
    caplog.set_level("WARNING", logger="src.agent.main")
    with pytest.raises(SystemExit) as exc_info:
        agent_main._diagnostic_signal_handler(signal.SIGTERM, None)
    assert exc_info.value.code == 128 + signal.SIGTERM
    assert any(
        "agent.signal_received" in r.message and "SIGTERM" in r.message and "phase=pre-mqtt-loop" in r.message
        for r in caplog.records
    )


def test_diagnostic_signal_handler_handles_unknown_signum(restore_signal_handlers, caplog):
    caplog.set_level("WARNING", logger="src.agent.main")
    # 9999 is not a real signal number; the handler must not crash.
    with pytest.raises(SystemExit):
        agent_main._diagnostic_signal_handler(9999, None)
    assert any("agent.signal_received" in r.message and "9999" in r.message for r in caplog.records)


def test_install_handles_unsupported_signal_gracefully(restore_signal_handlers):
    # Force signal.signal to raise for SIGQUIT to simulate a platform that
    # doesn't support it; the installer should swallow and continue.
    real_signal = signal.signal

    def fake_signal(sig, handler):
        if sig is signal.SIGQUIT:
            raise OSError("not supported")
        return real_signal(sig, handler)

    with patch.object(signal, "signal", side_effect=fake_signal):
        agent_main.install_diagnostic_signal_handlers()  # must not raise


def test_main_installs_handlers_before_entering_worker():
    """The handler must be installed before run_worker is called so that any
    signal arriving during settings load or worker setup is captured."""
    call_order: list[str] = []

    def fake_install():
        call_order.append("install")

    def fake_load_settings():
        call_order.append("load_settings")
        return object()

    def fake_run_worker(_settings):
        call_order.append("run_worker")
        return 0

    with (
        patch.object(agent_main, "install_diagnostic_signal_handlers", side_effect=fake_install),
        patch.object(agent_main, "load_worker_settings", side_effect=fake_load_settings),
        patch.object(agent_main, "run_worker", side_effect=fake_run_worker),
        patch.object(agent_main, "parse_args", return_value=type("A", (), {"log_level": "INFO"})()),
        patch.object(agent_main, "configure_logging"),
    ):
        agent_main.main()

    assert call_order == ["install", "load_settings", "run_worker"]
