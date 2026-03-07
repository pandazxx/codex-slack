from __future__ import annotations

from src.master.registry import AgentRegistry
from src.master.service import MasterService


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def build_image(self, *, name: str, repo_path: str, context_rel: str, dockerfile_rel: str) -> str:
        self.calls.append(("build_image", {"name": name, "repo_path": repo_path}))
        return f"codex-agent-{name}:latest"

    def create_or_update_agent(
        self,
        *,
        container_name: str,
        image: str,
        repo_volume: str,
        env: dict[str, str] | None = None,
        mounts: list[str] | None = None,
    ) -> None:
        self.calls.append(
            (
                "create_or_update_agent",
                {"container_name": container_name, "image": image, "env": env or {}, "mounts": mounts or []},
            )
        )

    def start_agent(self, name: str) -> None:
        self.calls.append(("start_agent", name))

    def stop_agent(self, name: str) -> None:
        self.calls.append(("stop_agent", name))

    def remove_agent(self, name: str) -> None:
        self.calls.append(("remove_agent", name))

    def refresh_agent_auth(self, *, volume_name: str, host_auth_path: str) -> None:
        self.calls.append(("refresh_agent_auth", {"volume_name": volume_name, "host_auth_path": host_auth_path}))

    def inspect_agent(self, name: str) -> dict[str, str] | None:
        self.calls.append(("inspect_agent", name))
        return {"Name": name, "State": "running"}

    def tail_logs(self, name: str, lines: int) -> str:
        self.calls.append(("tail_logs", {"name": name, "lines": lines}))
        return ""


class FailingRuntime(FakeRuntime):
    def start_agent(self, name: str) -> None:
        raise RuntimeError("podman start failed")

    def refresh_agent_auth(self, *, volume_name: str, host_auth_path: str) -> None:
        raise RuntimeError("auth refresh failed")


def test_load_agent_detects_dockerfile_plan(tmp_path) -> None:
    repo = tmp_path / "repo"
    dockerfile = repo / ".prj_assistant" / "image" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM alpine:3.20\n", encoding="utf-8")

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert result.ok is True
    assert result.data["image_plan"]["type"] == "dockerfile"


def test_load_agent_channel_conflict(tmp_path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    first = service.load_agent(name="payments-api", repo_path=str(repo_a), channel_id="C123")
    assert first.ok is True

    second = service.load_agent(name="billing-api", repo_path=str(repo_b), channel_id="C123")
    assert second.ok is False
    assert second.code == "ERR_CHANNEL_CONFLICT"


def test_start_agent_builds_on_start(tmp_path) -> None:
    repo = tmp_path / "repo"
    dockerfile = repo / ".prj_assistant" / "image" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM alpine:3.20\n", encoding="utf-8")

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    start_result = service.start_agent(name="payments-api")
    assert start_result.ok is True
    assert start_result.data["resolved_image"] == "codex-agent-payments-api:latest"
    assert runtime.calls[0][0] == "build_image"
    assert runtime.calls[1][0] == "create_or_update_agent"
    assert runtime.calls[1][1]["env"]["CODEX_CONTAINER_MODE"] == "agent-worker"
    assert runtime.calls[2] == ("start_agent", "agent-payments-api")


def test_start_agent_uses_configured_default_image(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime, default_image="codex-slack-v1-uat")

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    start_result = service.start_agent(name="payments-api")
    assert start_result.ok is True
    assert start_result.data["resolved_image"] == "codex-slack-v1-uat"
    assert runtime.calls[0][0] == "create_or_update_agent"
    assert runtime.calls[0][1]["image"] == "codex-slack-v1-uat"


def test_start_agent_passes_through_shared_auth_env(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setenv("GH_TOKEN", "gh-token-value")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    start_result = service.start_agent(name="payments-api")
    assert start_result.ok is True
    assert runtime.calls[0][0] == "create_or_update_agent"
    env = runtime.calls[0][1]["env"]
    assert env["CODEX_HOME"] == "/workspace/.codex"
    assert env["GH_TOKEN"] == "gh-token-value"
    assert env["OPENAI_API_KEY"] == "openai-key"


def test_start_agent_passes_git_identity_to_agent(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(
        registry=registry,
        runtime=runtime,
        git_user_name="Test User",
        git_user_email="test@example.com",
    )

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    start_result = service.start_agent(name="payments-api")
    assert start_result.ok is True
    env = runtime.calls[0][1]["env"]
    assert env["AGENT_GIT_USER_NAME"] == "Test User"
    assert env["AGENT_GIT_USER_EMAIL"] == "test@example.com"


def test_start_agent_mounts_codex_auth_json_only(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(
        registry=registry,
        runtime=runtime,
        agent_codex_auth_json_path="/host/secrets/codex-auth.json",
    )

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    start_result = service.start_agent(name="payments-api")
    assert start_result.ok is True
    assert runtime.calls[0][0] == "create_or_update_agent"
    mounts = runtime.calls[0][1]["mounts"]
    assert mounts == ["/host/secrets/codex-auth.json:/run/secrets/codex_auth.json:ro"]


def test_start_agent_mounts_ssh_forwarding_and_sets_env(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(
        registry=registry,
        runtime=runtime,
        agent_ssh_auth_sock_path="/run/user/1000/keyring/ssh",
        agent_ssh_known_hosts_path="/home/tester/.ssh/known_hosts",
    )

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    start_result = service.start_agent(name="payments-api")
    assert start_result.ok is True
    assert runtime.calls[0][0] == "create_or_update_agent"
    env = runtime.calls[0][1]["env"]
    mounts = runtime.calls[0][1]["mounts"]
    assert env["SSH_AUTH_SOCK"] == "/run/secrets/ssh-auth.sock"
    assert env["GIT_SSH_COMMAND"] == "ssh -o UserKnownHostsFile=/run/secrets/ssh_known_hosts"
    assert mounts == [
        "/run/user/1000/keyring/ssh:/run/secrets/ssh-auth.sock",
        "/home/tester/.ssh/known_hosts:/run/secrets/ssh_known_hosts:ro",
    ]


def test_start_agent_sets_insecure_default_ssh_config_without_known_hosts(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(
        registry=registry,
        runtime=runtime,
        agent_ssh_auth_sock_path="/run/user/1000/keyring/ssh",
    )

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    start_result = service.start_agent(name="payments-api")
    assert start_result.ok is True
    env = runtime.calls[0][1]["env"]
    mounts = runtime.calls[0][1]["mounts"]
    assert env["SSH_AUTH_SOCK"] == "/run/secrets/ssh-auth.sock"
    assert env["GIT_SSH_COMMAND"] == "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    assert mounts == ["/run/user/1000/keyring/ssh:/run/secrets/ssh-auth.sock"]


def test_run_git_uses_insecure_default_ssh_config_without_known_hosts(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(
        registry=registry,
        runtime=runtime,
        agent_ssh_auth_sock_path="/run/user/1000/keyring/ssh",
    )

    seen: dict[str, object] = {}

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        seen["args"] = args
        seen["env"] = kwargs.get("env")
        return __import__("subprocess").CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("src.master.service.subprocess.run", fake_run)

    service._run_git(["git", "clone", "git@github.com:org/repo.git", "/tmp/repo"])

    env = seen["env"]
    assert env["SSH_AUTH_SOCK"] == "/ssh-agent"
    assert env["GIT_SSH_COMMAND"] == "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"


def test_status_returns_registry_and_runtime(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")

    result = service.status(name="payments-api")
    assert result.ok is True
    assert result.data["record"]["name"] == "payments-api"
    assert result.data["runtime"]["Name"] == "agent-payments-api"


def test_refresh_agent_auth_reseeds_workspace_codex_home(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(
        registry=registry,
        runtime=runtime,
        agent_codex_auth_json_path="/host/secrets/codex-auth.json",
    )

    loaded = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert loaded.ok is True

    refreshed = service.refresh_agent_auth(name="payments-api")
    assert refreshed.ok is True
    assert refreshed.data["refreshed"] is True
    assert runtime.calls[-1] == (
        "refresh_agent_auth",
        {
            "volume_name": "agent-workspace-payments-api",
            "host_auth_path": "/host/secrets/codex-auth.json",
        },
    )


def test_refresh_agent_auth_requires_configured_auth_source(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    loaded = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert loaded.ok is True

    refreshed = service.refresh_agent_auth(name="payments-api")
    assert refreshed.ok is False
    assert refreshed.code == "ERR_RUNTIME_FAILED"


def test_refresh_agent_auth_propagates_runtime_failure(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FailingRuntime()
    service = MasterService(
        registry=registry,
        runtime=runtime,
        agent_codex_auth_json_path="/host/secrets/codex-auth.json",
    )

    loaded = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert loaded.ok is True

    refreshed = service.refresh_agent_auth(name="payments-api")
    assert refreshed.ok is False
    assert refreshed.code == "ERR_RUNTIME_FAILED"


def test_load_agent_invalid_name_returns_invalid_args(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    result = service.load_agent(name="BadName", repo_path=str(repo), channel_id="C123")
    assert result.ok is False
    assert result.code == "ERR_INVALID_ARGS"
    assert result.data["field"] == "name"


def test_load_agent_accepts_private_channel_id_prefix_g(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="G123")
    assert result.ok is True


def test_start_agent_runtime_failure_sets_error_state(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FailingRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    loaded = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert loaded.ok is True

    started = service.start_agent(name="payments-api")
    assert started.ok is False
    assert started.code == "ERR_RUNTIME_FAILED"

    saved = registry.get("payments-api")
    assert saved is not None
    assert saved.status == "error"
    assert saved.last_error == "podman start failed"


def test_load_agent_accepts_github_shorthand(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    monkeypatch.setattr(
        service,
        "_checkout_repo_source_with_fallback",
        lambda *, name, repo_source, repo_ref: (repo, repo_ref),  # type: ignore[no-untyped-def]
    )

    result = service.load_agent(name="payments-api", repo_path="pandazxx/aidotfile", channel_id="C123")
    assert result.ok is True

    saved = registry.get("payments-api")
    assert saved is not None
    assert saved.repo_source == "https://github.com/pandazxx/aidotfile.git"
    assert saved.repo_path == str(repo)


def test_load_agent_falls_back_to_master_when_main_missing(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    calls: list[str] = []

    def fake_checkout(*, name: str, repo_source: str, repo_ref: str):  # type: ignore[no-untyped-def]
        calls.append(repo_ref)
        if repo_ref == "main":
            raise RuntimeError("fatal: Remote branch main not found in upstream origin")
        return repo

    monkeypatch.setattr(service, "_checkout_repo_source", fake_checkout)

    result = service.load_agent(name="payments-api", repo_path="pandazxx/touchfish_agent", channel_id="C123")
    assert result.ok is True
    assert calls == ["main", "master"]

    saved = registry.get("payments-api")
    assert saved is not None
    assert saved.repo_ref == "master"


def test_load_agent_accepts_explicit_branch(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir()

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    seen: list[str] = []

    def fake_checkout(*, name: str, repo_source: str, repo_ref: str):  # type: ignore[no-untyped-def]
        seen.append(repo_ref)
        return repo

    monkeypatch.setattr(service, "_checkout_repo_source", fake_checkout)

    result = service.load_agent(
        name="payments-api",
        repo_path="pandazxx/touchfish_agent",
        channel_id="C123",
        repo_ref="master",
    )
    assert result.ok is True
    assert seen == ["master"]
    assert result.data["repo_ref"] == "master"


def test_load_agent_clears_stale_repo_cache_before_checkout(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "fresh-repo"
    repo.mkdir()
    stale_cache = tmp_path / "repo-cache" / "payments-api"
    stale_dockerfile = stale_cache / ".prj_assistant" / "image" / "Dockerfile"
    stale_dockerfile.parent.mkdir(parents=True)
    stale_dockerfile.write_text("FROM alpine:3.20\n", encoding="utf-8")

    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    monkeypatch.setattr(service, "_normalize_repo_source", lambda _value: "https://github.com/example/repo.git")

    def fake_checkout(*, name: str, repo_source: str, repo_ref: str):  # type: ignore[no-untyped-def]
        assert not stale_cache.exists()
        return repo

    monkeypatch.setattr(service, "_checkout_repo_source", fake_checkout)

    result = service.load_agent(name="payments-api", repo_path="example/repo", channel_id="C123")

    assert result.ok is True
    assert result.data["image_plan"]["type"] == "default"


def test_remove_agent_cleans_repo_cache(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = AgentRegistry(tmp_path / "agents.json")
    runtime = FakeRuntime()
    service = MasterService(registry=registry, runtime=runtime)

    load_result = service.load_agent(name="payments-api", repo_path=str(repo), channel_id="C123")
    assert load_result.ok is True

    stale_cache = tmp_path / "repo-cache" / "payments-api"
    stale_cache.mkdir(parents=True)
    (stale_cache / "README.md").write_text("stale\n", encoding="utf-8")

    removed = service.remove_agent(name="payments-api")

    assert removed.ok is True
    assert stale_cache.exists() is False
