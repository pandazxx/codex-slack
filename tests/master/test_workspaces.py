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


def test_create_workspace_inserts_default_staffs(client):
    r = create_ws(client)
    staffs = {s["name"]: s for s in r.json()["staffs"]}
    assert "claude" in staffs
    assert "codex" in staffs
    assert staffs["claude"]["adapter"] == "claude-code"
    assert staffs["codex"]["adapter"] == "codex"
    assert staffs["claude"]["is_default"] is True
    assert staffs["codex"]["is_default"] is False


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


# --- list_workspace_branches: GH_TOKEN resolution ---


def test_branches_uses_env_gh_token_when_set(client, monkeypatch):
    """If GH_TOKEN is in env, list_workspace_branches passes it to the fetcher."""
    monkeypatch.setenv("GH_TOKEN", "env-token")
    # Rebuild settings to pick up the env var (the client fixture started
    # the app before this monkeypatch).
    from src.master.config import load_master_settings

    client.app.state.settings = load_master_settings()

    ws_id = create_ws(client, name="env-token-ws", repo_url="https://github.com/x/y").json()["id"]
    with patch("src.master.workspaces._fetch_github_branches", return_value=["main"]) as m:
        r = client.get(f"/api/workspaces/{ws_id}/branches")
    assert r.status_code == 200
    assert r.json() == ["main"]
    m.assert_called_once_with("x", "y", "env-token")


def test_branches_falls_back_to_db_gh_token(client, monkeypatch):
    """Regression: when GH_TOKEN is unset in env but configured via the UI
    (runtime_config DB), list_workspace_branches must use the DB value
    instead of calling the GitHub API unauthenticated. Without this fix,
    private repos return 502."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    from src.master.config import load_master_settings

    client.app.state.settings = load_master_settings()
    assert client.app.state.settings.gh_token is None

    ws_id = create_ws(client, name="db-token-ws", repo_url="https://github.com/x/y").json()["id"]

    # Configure GH_TOKEN via the global runtime-config endpoint (mirrors the UI path).
    cfg_resp = client.patch("/api/config", json={"set": {"GH_TOKEN": "db-token"}})
    assert cfg_resp.status_code == 200, cfg_resp.text

    with patch("src.master.workspaces._fetch_github_branches", return_value=["main"]) as m:
        r = client.get(f"/api/workspaces/{ws_id}/branches")
    assert r.status_code == 200, r.text
    m.assert_called_once_with("x", "y", "db-token")


def test_branches_db_token_used_in_git_lsremote_fallback(client, monkeypatch):
    """If the REST API fetcher raises, the git ls-remote fallback must
    also receive the DB-resolved token (so a private repo can still
    succeed via the fallback if the API path fails for any reason)."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    from src.master.config import load_master_settings

    client.app.state.settings = load_master_settings()
    ws_id = create_ws(
        client, name="lsremote-ws", repo_url="https://github.com/x/y"
    ).json()["id"]
    cfg_resp = client.patch("/api/config", json={"set": {"GH_TOKEN": "db-token"}})
    assert cfg_resp.status_code == 200, cfg_resp.text

    with patch(
        "src.master.workspaces._fetch_github_branches", side_effect=RuntimeError("api down")
    ), patch("src.master.workspaces._fetch_git_branches", return_value=["main"]) as m:
        r = client.get(f"/api/workspaces/{ws_id}/branches")
    assert r.status_code == 200, r.text
    m.assert_called_once_with("https://github.com/x/y", "db-token")
