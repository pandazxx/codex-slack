from __future__ import annotations

import pytest

from src.master.config import load_master_settings


def test_load_master_settings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.setenv("MASTER_ADMIN_CHANNELS", "C123,C999")
    monkeypatch.setenv("MASTER_REGISTRY_PATH", "data/master/test.json")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")

    settings = load_master_settings()
    assert settings.slack_bot_token == "xoxb-token"
    assert settings.slack_app_token == "xapp-token"
    assert settings.admin_channels == {"C123", "C999"}
    assert settings.registry_path == "data/master/test.json"
    assert settings.dry_run is True


def test_load_master_settings_requires_admin_channels(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.delenv("MASTER_ADMIN_CHANNELS", raising=False)

    with pytest.raises(ValueError):
        load_master_settings()
