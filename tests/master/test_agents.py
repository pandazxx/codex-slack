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


# --- list ---

def test_list_agents_returns_defaults(client):
    ws = _make_ws(client)
    r = client.get(f"/api/workspaces/{ws['id']}/agents")
    assert r.status_code == 200
    names = {a["agent_name"] for a in r.json()}
    assert "claude" in names
    assert "codex" in names


def test_list_agents_unknown_workspace(client):
    r = client.get("/api/workspaces/no-such/agents")
    assert r.status_code == 404


# --- add ---

def test_add_agent_returns_201(client):
    ws = _make_ws(client)
    r = client.post(f"/api/workspaces/{ws['id']}/agents", json={
        "agent_name": "engineer", "adapter": "claude-code", "subagent": "engineer"
    })
    assert r.status_code == 201
    body = r.json()
    assert body["agent_name"] == "engineer"
    assert body["adapter"] == "claude-code"
    assert body["subagent"] == "engineer"
    assert body["active"] is True


def test_add_agent_appears_in_list(client):
    ws = _make_ws(client)
    client.post(f"/api/workspaces/{ws['id']}/agents", json={
        "agent_name": "tester", "adapter": "codex"
    })
    names = {a["agent_name"] for a in client.get(f"/api/workspaces/{ws['id']}/agents").json()}
    assert "tester" in names


def test_add_agent_duplicate_returns_409(client):
    ws = _make_ws(client)
    client.post(f"/api/workspaces/{ws['id']}/agents", json={"agent_name": "eng", "adapter": "claude-code"})
    r = client.post(f"/api/workspaces/{ws['id']}/agents", json={"agent_name": "eng", "adapter": "codex"})
    assert r.status_code == 409


def test_add_agent_invalid_adapter(client):
    ws = _make_ws(client)
    r = client.post(f"/api/workspaces/{ws['id']}/agents", json={"agent_name": "x", "adapter": "gpt4"})
    assert r.status_code == 422


def test_add_agent_lowercases_name(client):
    ws = _make_ws(client)
    r = client.post(f"/api/workspaces/{ws['id']}/agents", json={"agent_name": "Engineer", "adapter": "claude-code"})
    assert r.json()["agent_name"] == "engineer"


def test_add_agent_reactivates_after_remove(client):
    ws = _make_ws(client)
    r = client.post(f"/api/workspaces/{ws['id']}/agents", json={"agent_name": "eng", "adapter": "claude-code"})
    agent_id = r.json()["id"]
    client.delete(f"/api/workspaces/{ws['id']}/agents/{agent_id}")
    r2 = client.post(f"/api/workspaces/{ws['id']}/agents", json={"agent_name": "eng", "adapter": "codex"})
    assert r2.status_code == 201
    assert r2.json()["adapter"] == "codex"
    names = {a["agent_name"] for a in client.get(f"/api/workspaces/{ws['id']}/agents").json()}
    assert "eng" in names


# --- remove ---

def test_remove_agent_returns_204(client):
    ws = _make_ws(client)
    agent_id = next(a["id"] for a in client.get(f"/api/workspaces/{ws['id']}/agents").json()
                    if a["agent_name"] == "codex")
    r = client.delete(f"/api/workspaces/{ws['id']}/agents/{agent_id}")
    assert r.status_code == 204


def test_remove_agent_hides_from_list(client):
    ws = _make_ws(client)
    agent_id = next(a["id"] for a in client.get(f"/api/workspaces/{ws['id']}/agents").json()
                    if a["agent_name"] == "codex")
    client.delete(f"/api/workspaces/{ws['id']}/agents/{agent_id}")
    names = {a["agent_name"] for a in client.get(f"/api/workspaces/{ws['id']}/agents").json()}
    assert "codex" not in names


def test_remove_agent_not_found(client):
    ws = _make_ws(client)
    r = client.delete(f"/api/workspaces/{ws['id']}/agents/no-such")
    assert r.status_code == 404
