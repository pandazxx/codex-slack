from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.master.main import app, _db_path, _STATIC_DIR


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_schema_lists_all_tables(client):
    r = client.get("/schema")
    assert r.status_code == 200
    tables = r.json()
    assert set(tables.keys()) == {"workspaces", "workspace_agents", "topics", "sessions", "messages"}


def test_schema_workspaces_columns(client):
    r = client.get("/schema")
    cols = r.json()["workspaces"]
    assert "id" in cols
    assert "name" in cols
    assert "repo_url" in cols
    assert "created_at" in cols


def test_schema_workspace_agents_columns(client):
    r = client.get("/schema")
    cols = r.json()["workspace_agents"]
    for expected in ("id", "workspace_id", "agent_name", "adapter", "subagent", "active", "created_at", "deleted_at"):
        assert expected in cols, f"missing column: {expected}"


def test_db_file_created_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app):
        db = tmp_path / "master_data.db"
        assert db.exists(), "master_data.db was not created on startup"


def test_spa_returns_404_when_not_built(client):
    r = client.get("/some/spa/route")
    assert r.status_code == 404


def test_spa_serves_index_when_built(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    static = _STATIC_DIR
    index = static / "index.html"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("<!doctype html><html></html>")
    try:
        with TestClient(app) as c:
            r = c.get("/any/spa/path")
            assert r.status_code == 200
            assert "html" in r.text
    finally:
        index.unlink(missing_ok=True)
