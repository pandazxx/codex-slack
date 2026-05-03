from __future__ import annotations
from unittest.mock import MagicMock, patch

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


def create_ws(client, name="myrepo", repo_url="https://github.com/x/y"):
    return client.post("/api/workspaces", json={"name": name, "repo_url": repo_url})


# --- create ---

def test_create_workspace_returns_201(client):
    r = create_ws(client)
    assert r.status_code == 201


def test_create_workspace_body(client):
    r = create_ws(client, name="myrepo", repo_url="https://github.com/x/y")
    body = r.json()
    assert body["name"] == "myrepo"
    assert body["repo_url"] == "https://github.com/x/y"
    assert body["container_name"] == f"codex-agent-{body['id']}"
    assert "id" in body
    assert "created_at" in body


def test_create_workspace_inserts_default_agents(client):
    r = create_ws(client)
    agents = {a["agent_name"]: a for a in r.json()["agents"]}
    assert "claude" in agents
    assert "codex" in agents
    assert agents["claude"]["adapter"] == "claude-code"
    assert agents["codex"]["adapter"] == "codex"
    assert agents["claude"]["active"] is True


def test_create_workspace_duplicate_name_returns_409(client):
    create_ws(client, name="same")
    r = client.post("/api/workspaces", json={"name": "same", "repo_url": "https://github.com/x/z"})
    assert r.status_code == 409


def test_create_workspace_strips_whitespace(client):
    r = client.post("/api/workspaces", json={"name": "  repo  ", "repo_url": "  https://github.com/x/y  "})
    body = r.json()
    assert body["name"] == "repo"
    assert body["repo_url"] == "https://github.com/x/y"


# --- list ---

def test_list_workspaces_empty(client):
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    assert r.json() == []


def test_list_workspaces_returns_all(client):
    create_ws(client, name="a", repo_url="https://github.com/x/a")
    create_ws(client, name="b", repo_url="https://github.com/x/b")
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    names = {w["name"] for w in r.json()}
    assert names == {"a", "b"}


# --- get ---

def test_get_workspace_by_id(client):
    ws_id = create_ws(client).json()["id"]
    r = client.get(f"/api/workspaces/{ws_id}")
    assert r.status_code == 200
    assert r.json()["id"] == ws_id


def test_get_workspace_not_found(client):
    r = client.get("/api/workspaces/does-not-exist")
    assert r.status_code == 404


# --- delete ---

def test_delete_workspace_returns_204(client):
    ws_id = create_ws(client).json()["id"]
    r = client.delete(f"/api/workspaces/{ws_id}")
    assert r.status_code == 204


def test_delete_workspace_removes_it(client):
    ws_id = create_ws(client).json()["id"]
    client.delete(f"/api/workspaces/{ws_id}")
    # GET still returns the archived workspace, but with archived_at set
    r = client.get(f"/api/workspaces/{ws_id}")
    assert r.status_code == 200
    assert r.json()["archived_at"] is not None
    # Active list no longer includes it
    assert all(w["id"] != ws_id for w in client.get("/api/workspaces").json())
    # Archived list does include it
    assert any(w["id"] == ws_id for w in client.get("/api/workspaces?archived=true").json())


def test_delete_workspace_not_found(client):
    r = client.delete("/api/workspaces/does-not-exist")
    assert r.status_code == 404


def test_workspace_name_reuse_after_archive(client):
    ws_id = create_ws(client, name="recyclable").json()["id"]
    client.delete(f"/api/workspaces/{ws_id}")
    r = create_ws(client, name="recyclable")
    assert r.status_code == 201
    assert r.json()["name"] == "recyclable"


# --- agent-status ---

def test_agent_status_dry_run(client):
    ws_id = create_ws(client).json()["id"]
    r = client.get(f"/api/workspaces/{ws_id}/agent-status")
    assert r.status_code == 200
    assert r.json()["status"] == "dry_run"


def test_agent_status_not_found(client):
    r = client.get("/api/workspaces/does-not-exist/agent-status")
    assert r.status_code == 404
