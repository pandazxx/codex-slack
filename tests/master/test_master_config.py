from __future__ import annotations

import pytest

from src.master.config import load_master_settings


def _load(monkeypatch, **overrides):
    defaults = {
        "MASTER_DATA_DIR": "/tmp/test",
        "MASTER_DRY_RUN": "false",
        "MASTER_AGENT_BASE_IMAGE": "img:latest",
        "MASTER_CODEX_AUTH_JSON_PATH": "",
        "MASTER_SSH_AUTH_SOCK_PATH": "",
        "MASTER_SSH_KNOWN_HOSTS_PATH": "",
        "MASTER_GIT_USER_NAME": "",
        "MASTER_GIT_USER_EMAIL": "",
        "MQTT_HOST": "mosquitto",
        "MQTT_PORT": "1883",
        "MASTER_PORT": "8080",
        "CONTAINER_RUNTIME": "docker",
    }
    for k, v in {**defaults, **overrides}.items():
        monkeypatch.setenv(k, v)
    return load_master_settings()


def test_defaults(monkeypatch):
    s = _load(monkeypatch)
    assert s.mqtt_host == "mosquitto"
    assert s.mqtt_port == 1883
    assert s.master_port == 8080
    assert s.dry_run is False
    assert s.container_runtime == "docker"


def test_dry_run_true(monkeypatch):
    s = _load(monkeypatch, MASTER_DRY_RUN="true")
    assert s.dry_run is True


def test_optional_fields_empty_become_none(monkeypatch):
    s = _load(monkeypatch)
    assert s.agent_codex_auth_json_path is None
    assert s.agent_ssh_auth_sock_path is None
    assert s.git_user_name is None
    assert s.git_user_email is None


def test_optional_fields_set(monkeypatch):
    s = _load(monkeypatch, MASTER_GIT_USER_NAME="bot", MASTER_GIT_USER_EMAIL="bot@x.com")
    assert s.git_user_name == "bot"
    assert s.git_user_email == "bot@x.com"


def test_invalid_container_runtime_raises(monkeypatch):
    with pytest.raises(ValueError, match="CONTAINER_RUNTIME"):
        _load(monkeypatch, CONTAINER_RUNTIME="kubernetes")


def test_docker_runtime_accepted(monkeypatch):
    s = _load(monkeypatch, CONTAINER_RUNTIME="docker")
    assert s.container_runtime == "docker"
