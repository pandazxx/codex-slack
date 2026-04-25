from __future__ import annotations

import pytest

from src.master.config import load_master_settings


def test_load_master_settings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MASTER_FRONTENDS", "slack,discord")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")
    monkeypatch.setenv("MASTER_ADMIN_CHANNELS", "C123,C999")
    monkeypatch.setenv("DISCORD_ADMIN_CHANNELS", "123456789012345678")
    monkeypatch.setenv("MASTER_REGISTRY_PATH", "data/master/test.json")
    monkeypatch.setenv("MASTER_THREAD_STATE_PATH", "data/master/thread-state.json")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    monkeypatch.setenv("MASTER_AGENT_BASE_IMAGE", "codex-slack-v1-uat")
    monkeypatch.setenv("MASTER_CODEX_AUTH_JSON_PATH", "/host/secrets/codex-auth.json")
    monkeypatch.setenv("MASTER_CODEX_CONFIG_DIR_PATH", "/host/config/codex")
    monkeypatch.setenv("MASTER_CLAUDE_CONFIG_DIR_PATH", "/host/config/claude")
    monkeypatch.setenv("MASTER_SSH_AUTH_SOCK_PATH", "/run/user/1000/keyring/ssh")
    monkeypatch.setenv("MASTER_SSH_KNOWN_HOSTS_PATH", "/home/tester/.ssh/known_hosts")
    monkeypatch.setenv("MASTER_GIT_USER_NAME", "Test User")
    monkeypatch.setenv("MASTER_GIT_USER_EMAIL", "test@example.com")
    monkeypatch.setenv("MASTER_AGENT_COMMAND_TEMPLATE", "codex exec resume abc -")
    monkeypatch.setenv("MASTER_CLAUDE_COMMAND_TEMPLATE", "claude --print -")
    monkeypatch.setenv("MASTER_DEFAULT_AGENT_ADAPTER", "claude-code")
    monkeypatch.setenv("MASTER_AGENT_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("MASTER_AGENT_AUTH_REFRESH_MAX_AGE_DAYS", "14")
    monkeypatch.setenv("MASTER_COMMAND_RATE_LIMIT_COUNT", "10")
    monkeypatch.setenv("MASTER_COMMAND_RATE_LIMIT_WINDOW_SECONDS", "45")

    settings = load_master_settings()
    assert settings.frontends == {"slack", "discord"}
    assert settings.slack_bot_token == "xoxb-token"
    assert settings.slack_app_token == "xapp-token"
    assert settings.discord_bot_token == "discord-token"
    assert settings.admin_channels == {"C123", "C999"}
    assert settings.discord_admin_channels == {"123456789012345678"}
    assert settings.registry_path == "data/master/test.json"
    assert settings.thread_state_path == "data/master/thread-state.json"
    assert settings.dry_run is True
    assert settings.agent_base_image == "codex-slack-v1-uat"
    assert settings.agent_codex_auth_json_path == "/host/secrets/codex-auth.json"
    assert settings.agent_codex_config_dir_path == "/host/config/codex"
    assert settings.agent_claude_config_dir_path == "/host/config/claude"
    assert settings.agent_ssh_auth_sock_path == "/run/user/1000/keyring/ssh"
    assert settings.agent_ssh_known_hosts_path == "/home/tester/.ssh/known_hosts"
    assert settings.git_user_name == "Test User"
    assert settings.git_user_email == "test@example.com"
    assert settings.codex_command_template == "codex exec resume abc -"
    assert settings.claude_command_template == "claude --print -"
    assert settings.default_agent_adapter == "claude-code"
    assert settings.dispatch_timeout_seconds == 30
    assert settings.auth_refresh_max_age_days == 14
    assert settings.command_rate_limit_count == 10
    assert settings.command_rate_limit_window_seconds == 45


def test_load_master_settings_requires_admin_channels(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MASTER_FRONTENDS", "slack")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.delenv("MASTER_ADMIN_CHANNELS", raising=False)

    with pytest.raises(ValueError):
        load_master_settings()


def test_load_master_settings_uses_session_aware_default_template(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MASTER_FRONTENDS", "slack")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.setenv("MASTER_ADMIN_CHANNELS", "C123")
    monkeypatch.delenv("MASTER_AGENT_COMMAND_TEMPLATE", raising=False)

    settings = load_master_settings()

    assert settings.thread_state_path == "data/master/thread_state.json"
    assert settings.codex_command_template == "codex exec --dangerously-bypass-approvals-and-sandbox resume --last -"
    assert settings.claude_command_template == "claude -p --output-format json --dangerously-skip-permissions"
    assert settings.default_agent_adapter == "codex"
    assert settings.auth_refresh_max_age_days == 7


def test_load_master_settings_rejects_unknown_default_agent_adapter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MASTER_FRONTENDS", "slack")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.setenv("MASTER_ADMIN_CHANNELS", "C123")
    monkeypatch.setenv("MASTER_DEFAULT_AGENT_ADAPTER", "unknown")

    with pytest.raises(ValueError, match="MASTER_DEFAULT_AGENT_ADAPTER"):
        load_master_settings()


def test_load_master_settings_auto_detects_project_config_dirs(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    project_dir = tmp_path / "project"
    (project_dir / "config" / "codex-global").mkdir(parents=True)
    (project_dir / "config" / "claude-global").mkdir(parents=True)

    monkeypatch.setenv("MASTER_FRONTENDS", "slack")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-token")
    monkeypatch.setenv("MASTER_ADMIN_CHANNELS", "C123")
    monkeypatch.setenv("MASTER_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("MASTER_CODEX_CONFIG_DIR_PATH", raising=False)
    monkeypatch.delenv("MASTER_CLAUDE_CONFIG_DIR_PATH", raising=False)

    settings = load_master_settings()

    assert settings.agent_codex_config_dir_path == str(project_dir / "config" / "codex-global")
    assert settings.agent_claude_config_dir_path == str(project_dir / "config" / "claude-global")


def test_load_master_settings_discord_frontend_requires_discord_fields(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MASTER_FRONTENDS", "discord")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_ADMIN_CHANNELS", raising=False)

    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
        load_master_settings()
