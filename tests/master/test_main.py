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
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_schema_lists_all_tables(client):
    from src.master.db import TABLES
    r = client.get("/schema")
    assert r.status_code == 200
    assert set(r.json().keys()) == set(TABLES)


def test_schema_workspaces_columns(client):
    r = client.get("/schema")
    cols = r.json()["workspaces"]
    assert "id" in cols
    assert "name" in cols
    assert "repo_url" in cols
    assert "created_at" in cols


def test_schema_staffs_columns(client):
    r = client.get("/schema")
    cols = r.json()["staffs"]
    for expected in ("id", "scope_type", "scope_id", "name", "adapter", "model",
                     "system_prompt", "agent", "session_scope", "is_default"):
        assert expected in cols, f"missing column: {expected}"


def test_db_file_created_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app):
        db = tmp_path / "master_data.db"
        assert db.exists(), "master_data.db was not created on startup"


def test_spa_returns_404_when_not_built(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    fake_static = tmp_path / "static"
    fake_static.mkdir()
    monkeypatch.setattr("src.master.main._STATIC_DIR", fake_static)
    with TestClient(app) as c:
        r = c.get("/some/spa/route")
        assert r.status_code == 404


def test_spa_serves_index_when_built(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    fake_static = tmp_path / "static"
    fake_static.mkdir()
    (fake_static / "index.html").write_text("<!doctype html><html></html>")
    monkeypatch.setattr("src.master.main._STATIC_DIR", fake_static)
    with TestClient(app) as c:
        r = c.get("/any/spa/path")
        assert r.status_code == 200
        assert "html" in r.text
