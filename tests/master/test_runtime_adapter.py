from __future__ import annotations

import subprocess

import pytest

from src.master.runtime_adapter import PodmanRuntimeAdapter, RuntimeErrorAdapter


def test_runtime_adapter_reports_missing_podman(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    adapter = PodmanRuntimeAdapter()
    monkeypatch.setattr("src.master.runtime_adapter.shutil.which", lambda _: None)

    with pytest.raises(RuntimeErrorAdapter) as excinfo:
        adapter.start_agent("agent-payments-api")

    assert "podman CLI is not installed" in str(excinfo.value)


def test_runtime_adapter_dry_run_skips_binary_lookup(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    adapter = PodmanRuntimeAdapter(dry_run=True)
    monkeypatch.setattr("src.master.runtime_adapter.shutil.which", lambda _: None)

    completed = adapter._run(["podman", "ps"])

    assert isinstance(completed, subprocess.CompletedProcess)
    assert completed.returncode == 0
