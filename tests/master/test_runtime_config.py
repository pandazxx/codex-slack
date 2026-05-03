"""Tests for runtime config CRUD with global/workspace merge."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.master.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with TestClient(app) as c:
        yield c


def _make_ws(client, name="repo"):
    return client.post("/api/workspaces", json={"name": name, "repo_url": "https://github.com/x/y"}).json()


# ── Global config ─────────────────────────────────────────────────────────────

def test_global_config_empty(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json() == {}


def test_patch_global_config_set(client):
    r = client.patch("/api/config", json={"set": {"ANTHROPIC_API_KEY": "sk-test"}, "delete": []})
    assert r.status_code == 200
    assert r.json()["ANTHROPIC_API_KEY"] == "sk-test"


def test_patch_global_config_delete(client):
    client.patch("/api/config", json={"set": {"KEY": "val"}, "delete": []})
    r = client.patch("/api/config", json={"set": {}, "delete": ["KEY"]})
    assert r.status_code == 200
    assert "KEY" not in r.json()


def test_patch_global_config_upsert(client):
    client.patch("/api/config", json={"set": {"KEY": "v1"}, "delete": []})
    r = client.patch("/api/config", json={"set": {"KEY": "v2"}, "delete": []})
    assert r.json()["KEY"] == "v2"


def test_get_global_config_returns_all_keys(client):
    client.patch("/api/config", json={"set": {"A": "1", "B": "2"}, "delete": []})
    r = client.get("/api/config")
    assert r.json() == {"A": "1", "B": "2"}


# ── Workspace config ──────────────────────────────────────────────────────────

def test_workspace_config_empty(client):
    ws = _make_ws(client)
    r = client.get(f"/api/workspaces/{ws['id']}/config")
    assert r.status_code == 200
    assert r.json() == {}


def test_workspace_config_inherits_global(client):
    ws = _make_ws(client)
    client.patch("/api/config", json={"set": {"GLOBAL_KEY": "global-val"}, "delete": []})
    r = client.get(f"/api/workspaces/{ws['id']}/config")
    assert r.json()["GLOBAL_KEY"] == "global-val"


def test_workspace_config_overrides_global(client):
    ws = _make_ws(client)
    client.patch("/api/config", json={"set": {"KEY": "global"}, "delete": []})
    client.patch(f"/api/workspaces/{ws['id']}/config", json={"set": {"KEY": "workspace"}, "delete": []})
    r = client.get(f"/api/workspaces/{ws['id']}/config")
    assert r.json()["KEY"] == "workspace"


def test_workspace_config_patch_returns_merged(client):
    ws = _make_ws(client)
    client.patch("/api/config", json={"set": {"GLOBAL": "g"}, "delete": []})
    r = client.patch(f"/api/workspaces/{ws['id']}/config", json={"set": {"LOCAL": "l"}, "delete": []})
    assert r.status_code == 200
    body = r.json()
    assert body["GLOBAL"] == "g"
    assert body["LOCAL"] == "l"


def test_workspace_config_delete_local_key(client):
    ws = _make_ws(client)
    client.patch(f"/api/workspaces/{ws['id']}/config", json={"set": {"LOCAL": "l"}, "delete": []})
    r = client.patch(f"/api/workspaces/{ws['id']}/config", json={"set": {}, "delete": ["LOCAL"]})
    assert "LOCAL" not in r.json()


def test_workspace_config_not_found(client):
    r = client.get("/api/workspaces/no-such/config")
    assert r.status_code == 404


def test_workspace_config_patch_not_found(client):
    r = client.patch("/api/workspaces/no-such/config", json={"set": {"K": "v"}, "delete": []})
    assert r.status_code == 404


def test_global_config_delete_does_not_affect_workspace_override(client):
    ws = _make_ws(client)
    client.patch("/api/config", json={"set": {"KEY": "global"}, "delete": []})
    client.patch(f"/api/workspaces/{ws['id']}/config", json={"set": {"KEY": "ws"}, "delete": []})
    client.patch("/api/config", json={"set": {}, "delete": ["KEY"]})
    r = client.get(f"/api/workspaces/{ws['id']}/config")
    assert r.json().get("KEY") == "ws"
