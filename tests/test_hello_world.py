"""Test for GET /hello-world endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.master.main import app


@pytest.fixture()
def _base_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Minimal environment required to start the master FastAPI application."""
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")


def test_hello_world(_base_env) -> None:
    """GET /hello-world must return HTTP 200 with a hello world message."""
    with TestClient(app) as client:
        response = client.get("/hello-world")
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "hello world"
