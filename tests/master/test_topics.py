from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.master.main import app
from src.master.topics import _slugify


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def workspace_id(client):
    r = client.post("/api/workspaces", json={"name": "repo", "repo_url": "https://github.com/x/y"})
    return r.json()["id"]


def create_topic(client, workspace_id, subject="Fix the bug", branch_name=None):
    body = {"subject": subject}
    if branch_name:
        body["branch_name"] = branch_name
    return client.post(f"/api/workspaces/{workspace_id}/topics", json=body)


# --- slugify ---

def test_slugify_basic():
    assert _slugify("Fix the bug") == "fix-the-bug"


def test_slugify_special_chars():
    assert _slugify("Add feat: new/thing!") == "add-feat-newthing"


def test_slugify_truncates():
    assert len(_slugify("a" * 100)) <= 60


def test_slugify_empty_falls_back():
    assert _slugify("!!!") == "topic"


# --- create ---

def test_create_topic_returns_201(client, workspace_id):
    r = create_topic(client, workspace_id)
    assert r.status_code == 201


def test_create_topic_body(client, workspace_id):
    r = create_topic(client, workspace_id, subject="Fix the bug")
    body = r.json()
    assert body["subject"] == "Fix the bug"
    assert body["workspace_id"] == workspace_id
    assert body["branch_name"] == "fix-the-bug"
    assert body["worktree_path"].startswith("/workspace/worktrees/")
    assert "id" in body and "created_at" in body


def test_create_topic_custom_branch(client, workspace_id):
    r = create_topic(client, workspace_id, subject="My topic", branch_name="feat/my-branch")
    assert r.json()["branch_name"] == "feat/my-branch"


def test_create_topic_unknown_workspace(client):
    r = create_topic(client, "no-such-workspace")
    assert r.status_code == 404


# --- list ---

def test_list_topics_empty(client, workspace_id):
    r = client.get(f"/api/workspaces/{workspace_id}/topics")
    assert r.status_code == 200
    assert r.json() == []


def test_list_topics_returns_all(client, workspace_id):
    create_topic(client, workspace_id, subject="Topic A")
    create_topic(client, workspace_id, subject="Topic B")
    r = client.get(f"/api/workspaces/{workspace_id}/topics")
    assert r.status_code == 200
    subjects = {t["subject"] for t in r.json()}
    assert subjects == {"Topic A", "Topic B"}


def test_list_topics_unknown_workspace(client):
    r = client.get("/api/workspaces/no-such/topics")
    assert r.status_code == 404


# --- get ---

def test_get_topic_by_id(client, workspace_id):
    topic_id = create_topic(client, workspace_id).json()["id"]
    r = client.get(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.status_code == 200
    assert r.json()["id"] == topic_id


def test_get_topic_not_found(client, workspace_id):
    r = client.get(f"/api/workspaces/{workspace_id}/topics/no-such")
    assert r.status_code == 404


# --- delete ---

def test_delete_topic_returns_204(client, workspace_id):
    topic_id = create_topic(client, workspace_id).json()["id"]
    r = client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert r.status_code == 204


def test_delete_topic_removes_it(client, workspace_id):
    topic_id = create_topic(client, workspace_id).json()["id"]
    client.delete(f"/api/workspaces/{workspace_id}/topics/{topic_id}")
    assert client.get(f"/api/workspaces/{workspace_id}/topics/{topic_id}").status_code == 404
    assert all(t["id"] != topic_id for t in client.get(f"/api/workspaces/{workspace_id}/topics").json())


def test_delete_topic_not_found(client, workspace_id):
    r = client.delete(f"/api/workspaces/{workspace_id}/topics/no-such")
    assert r.status_code == 404
