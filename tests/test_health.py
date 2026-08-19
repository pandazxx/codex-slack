"""Integration tests for GET /health — version field.

These tests verify that the health endpoint returns the correct version string
derived from the APP_VERSION environment variable (see ADR 0011).

Note: tests/master/test_main.py contains a test_health test that asserts
{"status": "ok"} without a version field.  That assertion will need to be
updated by the engineer once src/master/main.py is patched to include the
version field.  The tests here cover the new behaviour explicitly.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.master.main import app


@pytest.fixture()
def _base_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Minimal environment required to start the master FastAPI application."""
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")


class TestHealthVersion:
    """Tests for the version field on GET /health."""

    def test_health_returns_version_when_app_version_set(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When APP_VERSION=test-1.2.3, GET /health must return
        {"status": "ok", "version": "test-1.2.3"}."""
        monkeypatch.setenv("APP_VERSION", "test-1.2.3")
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == "test-1.2.3"

    def test_health_returns_dev_when_app_version_unset(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When APP_VERSION is not set, GET /health must return
        {"status": "ok", "version": "dev"}."""
        monkeypatch.delenv("APP_VERSION", raising=False)
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == "dev"

    def test_health_returns_dev_when_app_version_empty(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When APP_VERSION='', GET /health must fall back to 'dev'."""
        monkeypatch.setenv("APP_VERSION", "")
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == "dev"

    def test_health_response_contains_exactly_status_and_version(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /health response body must contain exactly 'status' and 'version' keys."""
        monkeypatch.setenv("APP_VERSION", "v4.0-rc1")
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"status", "version"}

    def test_health_rc_string_passed_through_unchanged(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An RC-format version string (e.g. v4.0-rc3) is surfaced verbatim.
        This reflects the intentional behaviour described in ADR 0011: production
        images are retagged from RC images, so the version field will show the
        RC string, not the release string."""
        monkeypatch.setenv("APP_VERSION", "v4.0-rc3")
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["version"] == "v4.0-rc3"


class TestVersion:
    """Tests for the GET /version endpoint."""

    def test_version_returns_200(self, _base_env, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /version must return status 200."""
        monkeypatch.setenv("APP_VERSION", "test-1.2.3")
        with TestClient(app) as client:
            response = client.get("/version")
        assert response.status_code == 200

    def test_version_returns_version_when_app_version_set(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When APP_VERSION=test-1.2.3, GET /version must return
        {"version": "test-1.2.3"}."""
        monkeypatch.setenv("APP_VERSION", "test-1.2.3")
        with TestClient(app) as client:
            response = client.get("/version")
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "test-1.2.3"

    def test_version_returns_dev_when_app_version_unset(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When APP_VERSION is not set, GET /version must return
        {"version": "dev"}."""
        monkeypatch.delenv("APP_VERSION", raising=False)
        with TestClient(app) as client:
            response = client.get("/version")
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "dev"

    def test_version_returns_dev_when_app_version_empty(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When APP_VERSION='', GET /version must fall back to 'dev'."""
        monkeypatch.setenv("APP_VERSION", "")
        with TestClient(app) as client:
            response = client.get("/version")
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "dev"

    def test_version_response_contains_exactly_version(
        self, _base_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /version response body must contain exactly 'version' key."""
        monkeypatch.setenv("APP_VERSION", "v4.0-rc1")
        with TestClient(app) as client:
            response = client.get("/version")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"version"}
