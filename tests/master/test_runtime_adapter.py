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


def test_create_or_update_agent_recreates_existing_container(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    adapter = PodmanRuntimeAdapter()
    seen: list[list[str]] = []

    monkeypatch.setattr(adapter, "_container_exists", lambda _: True)

    def fake_run(cmd: list[str], cwd: str | None = None, check: bool = True):  # type: ignore[no-untyped-def]
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(adapter, "_run", fake_run)

    adapter.create_or_update_agent(
        container_name="agent-payments-api",
        image="codex-slack-bot:latest",
        repo_volume="agent-workspace-payments-api",
        env={"CODEX_CONTAINER_MODE": "agent-worker"},
    )

    assert seen[0] == ["podman", "rm", "-f", "agent-payments-api"]
    assert seen[1][0:4] == ["podman", "create", "--name", "agent-payments-api"]
    assert "-e" in seen[1]


def test_create_or_update_agent_adds_extra_mounts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    adapter = PodmanRuntimeAdapter()
    seen: list[list[str]] = []

    monkeypatch.setattr(adapter, "_container_exists", lambda _: False)

    def fake_run(cmd: list[str], cwd: str | None = None, check: bool = True):  # type: ignore[no-untyped-def]
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(adapter, "_run", fake_run)

    adapter.create_or_update_agent(
        container_name="agent-payments-api",
        image="codex-slack-bot:latest",
        repo_volume="agent-workspace-payments-api",
        mounts=["/host/auth.json:/run/secrets/codex_auth.json:ro"],
    )

    assert seen[0][0:8] == [
        "podman",
        "create",
        "--name",
        "agent-payments-api",
        "-v",
        "agent-workspace-payments-api:/workspace",
        "-v",
        "/host/auth.json:/run/secrets/codex_auth.json:ro",
    ]
