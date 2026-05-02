from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.master.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app) as c:
        yield c


def create_ws(client, name="myrepo", repo_url="https://github.com/x/y"):
    return client.post("/workspaces", json={"name": name, "repo_url": repo_url})


# --- create ---

def test_create_workspace_returns_201(client):
    r = create_ws(client)
    assert r.status_code == 201


def test_create_workspace_body(client):
    r = create_ws(client, name="myrepo", repo_url="https://github.com/x/y")
    body = r.json()
    assert body["name"] == "myrepo"
    assert body["repo_url"] == "https://github.com/x/y"
    assert body["container_name"] is None
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
    r = client.post("/workspaces", json={"name": "same", "repo_url": "https://github.com/x/z"})
    assert r.status_code == 409


def test_create_workspace_strips_whitespace(client):
    r = client.post("/workspaces", json={"name": "  repo  ", "repo_url": "  https://github.com/x/y  "})
    body = r.json()
    assert body["name"] == "repo"
    assert body["repo_url"] == "https://github.com/x/y"


# --- list ---

def test_list_workspaces_empty(client):
    r = client.get("/workspaces")
    assert r.status_code == 200
    assert r.json() == []


def test_list_workspaces_returns_all(client):
    create_ws(client, name="a", repo_url="https://github.com/x/a")
    create_ws(client, name="b", repo_url="https://github.com/x/b")
    r = client.get("/workspaces")
    assert r.status_code == 200
    names = {w["name"] for w in r.json()}
    assert names == {"a", "b"}


# --- get ---

def test_get_workspace_by_id(client):
    ws_id = create_ws(client).json()["id"]
    r = client.get(f"/workspaces/{ws_id}")
    assert r.status_code == 200
    assert r.json()["id"] == ws_id


def test_get_workspace_not_found(client):
    r = client.get("/workspaces/does-not-exist")
    assert r.status_code == 404


# --- delete ---

def test_delete_workspace_returns_204(client):
    ws_id = create_ws(client).json()["id"]
    r = client.delete(f"/workspaces/{ws_id}")
    assert r.status_code == 204


def test_delete_workspace_removes_it(client):
    ws_id = create_ws(client).json()["id"]
    client.delete(f"/workspaces/{ws_id}")
    assert client.get(f"/workspaces/{ws_id}").status_code == 404
    assert all(w["id"] != ws_id for w in client.get("/workspaces").json())


def test_delete_workspace_not_found(client):
    r = client.delete("/workspaces/does-not-exist")
    assert r.status_code == 404
