"""Unit tests for src/version.py — get_app_version()."""
from __future__ import annotations

import pytest

from src.version import get_app_version


class TestGetAppVersion:
    """Tests for get_app_version()."""

    def test_env_var_set_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When APP_VERSION is set to a non-empty string, that string is returned."""
        monkeypatch.setenv("APP_VERSION", "v4.0-rc1")
        assert get_app_version() == "v4.0-rc1"

    def test_env_var_set_to_arbitrary_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any non-empty, non-whitespace APP_VERSION is returned as-is."""
        monkeypatch.setenv("APP_VERSION", "sha-abc1234")
        assert get_app_version() == "sha-abc1234"

    def test_env_var_unset_returns_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When APP_VERSION is not set, the fallback 'dev' is returned."""
        monkeypatch.delenv("APP_VERSION", raising=False)
        assert get_app_version() == "dev"

    def test_env_var_empty_string_returns_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When APP_VERSION is set to an empty string, the fallback 'dev' is returned."""
        monkeypatch.setenv("APP_VERSION", "")
        assert get_app_version() == "dev"

    def test_env_var_whitespace_returns_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When APP_VERSION is set to whitespace only, the fallback 'dev' is returned."""
        monkeypatch.setenv("APP_VERSION", "  ")
        assert get_app_version() == "dev"

    def test_return_type_is_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_app_version() always returns a str, never None."""
        monkeypatch.delenv("APP_VERSION", raising=False)
        result = get_app_version()
        assert isinstance(result, str)

    def test_version_string_with_leading_trailing_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """APP_VERSION with surrounding whitespace around a real value is stripped to 'dev'
        only if the stripped result is empty; a non-empty stripped value is returned."""
        # A value like "  v1.0  " strips to "v1.0" which is non-empty — returned as stripped.
        monkeypatch.setenv("APP_VERSION", "  v1.0  ")
        assert get_app_version() == "v1.0"
