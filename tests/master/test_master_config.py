from __future__ import annotations

import pytest

from src.master.config import load_master_settings


def test_load_master_settings_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")

    settings = load_master_settings()

    assert settings.data_dir == str(tmp_path)
    assert settings.dry_run is False
    assert settings.container_runtime == "docker"
    assert settings.mqtt_host == "mosquitto"
    assert settings.mqtt_port == 1883
    assert settings.master_port == 8080
    assert settings.master_url == "http://master:8080"
    assert settings.agent_idle_timeout_seconds == 3600
    assert settings.agent_auth_refresh_interval_seconds == 43200
    assert settings.attachment_max_size_mb == 20
    assert settings.attachment_max_size_bytes == 20 * 1024 * 1024


def test_load_master_settings_explicit_values(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    monkeypatch.setenv("MASTER_AGENT_BASE_IMAGE", "my-image:latest")
    monkeypatch.setenv("MASTER_AGENT_NETWORK", "mynet")
    monkeypatch.setenv("MQTT_HOST", "broker.local")
    monkeypatch.setenv("MQTT_PORT", "1884")
    monkeypatch.setenv("MASTER_PORT", "9090")
    monkeypatch.setenv("MASTER_URL", "http://mymaster:9090")
    monkeypatch.setenv("MASTER_SSH_AUTH_SOCK_PATH", "/run/ssh.sock")
    monkeypatch.setenv("MASTER_SSH_KNOWN_HOSTS_PATH", "/etc/ssh/known_hosts")
    monkeypatch.setenv("GH_TOKEN", "ghp_token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")
    monkeypatch.setenv("AGENT_IDLE_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("AGENT_AUTH_REFRESH_INTERVAL_SECONDS", "7200")
    monkeypatch.setenv("ATTACHMENT_MAX_SIZE_MB", "50")

    settings = load_master_settings()

    assert settings.dry_run is True
    assert settings.agent_base_image == "my-image:latest"
    assert settings.agent_network == "mynet"
    assert settings.mqtt_host == "broker.local"
    assert settings.mqtt_port == 1884
    assert settings.master_port == 9090
    assert settings.master_url == "http://mymaster:9090"
    assert settings.agent_ssh_auth_sock_path == "/run/ssh.sock"
    assert settings.agent_ssh_known_hosts_path == "/etc/ssh/known_hosts"
    assert settings.gh_token == "ghp_token"
    assert settings.anthropic_api_key == "sk-ant-key"
    assert settings.agent_idle_timeout_seconds == 1800
    assert settings.agent_auth_refresh_interval_seconds == 7200
    assert settings.attachment_max_size_mb == 50


def test_load_master_settings_rejects_unknown_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "kubernetes")

    with pytest.raises(ValueError, match="CONTAINER_RUNTIME"):
        load_master_settings()


def test_load_master_settings_empty_optionals_become_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.delenv("MASTER_SSH_AUTH_SOCK_PATH", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    settings = load_master_settings()

    assert settings.agent_ssh_auth_sock_path is None
    assert settings.gh_token is None
    assert settings.anthropic_api_key is None
    assert settings.claude_code_oauth_token is None


def test_effective_attachment_data_dir_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.delenv("ATTACHMENT_DATA_DIR", raising=False)

    settings = load_master_settings()

    assert settings.effective_attachment_data_dir() == f"{tmp_path}/attachments"


def test_effective_attachment_data_dir_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("ATTACHMENT_DATA_DIR", "/mnt/uploads")

    settings = load_master_settings()

    assert settings.effective_attachment_data_dir() == "/mnt/uploads"
