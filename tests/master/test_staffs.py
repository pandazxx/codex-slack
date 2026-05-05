"""Tests for the staff CRUD and cascade resolution system."""
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


def _make_ws(client, name="repo"):
    return client.post("/api/workspaces", json={"name": name, "repo_url": "https://github.com/x/y"}).json()


def _make_topic(client, ws_id):
    return client.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "work", "repo_ref": "main"}).json()


# ── Global staff CRUD ─────────────────────────────────────────────────────────

def test_list_global_staffs_empty(client):
    r = client.get("/api/staffs")
    assert r.status_code == 200
    assert r.json() == []


def test_create_global_staff(client):
    r = client.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code", "is_default": True})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "reviewer"
    assert body["adapter"] == "claude-code"
    assert body["scope_type"] == "global"
    assert body["scope_id"] is None
    assert body["is_default"] is True
    assert body["inherited_from"] is None


def test_create_global_staff_appears_in_list(client):
    client.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code"})
    r = client.get("/api/staffs")
    assert any(s["name"] == "reviewer" for s in r.json())


def test_create_global_staff_duplicate_409(client):
    client.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code"})
    r = client.post("/api/staffs", json={"name": "reviewer", "adapter": "codex"})
    assert r.status_code == 409


def test_create_global_staff_invalid_adapter(client):
    r = client.post("/api/staffs", json={"name": "x", "adapter": "gpt4"})
    assert r.status_code == 422


def test_update_global_staff(client):
    client.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code"})
    r = client.put("/api/staffs/reviewer", json={"name": "reviewer", "adapter": "codex", "model": "o3"})
    assert r.status_code == 200
    assert r.json()["adapter"] == "codex"
    assert r.json()["model"] == "o3"


def test_delete_global_staff(client):
    client.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code"})
    r = client.delete("/api/staffs/reviewer")
    assert r.status_code == 204
    assert client.get("/api/staffs").json() == []


def test_delete_global_staff_not_found(client):
    r = client.delete("/api/staffs/no-such")
    assert r.status_code == 404


# ── Workspace staff CRUD ──────────────────────────────────────────────────────

def test_list_workspace_staffs_includes_defaults(client):
    ws = _make_ws(client)
    r = client.get(f"/api/workspaces/{ws['id']}/staffs")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert "claude" in names
    assert "codex" in names


def test_list_workspace_staffs_inherits_global(client):
    ws = _make_ws(client)
    client.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code"})
    r = client.get(f"/api/workspaces/{ws['id']}/staffs")
    inherited = {s["name"]: s for s in r.json() if s["inherited_from"] == "global"}
    assert "reviewer" in inherited


def test_workspace_staff_shadows_global(client):
    ws = _make_ws(client)
    client.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code", "model": "global-model"})
    client.post(f"/api/workspaces/{ws['id']}/staffs", json={
        "name": "reviewer", "adapter": "claude-code", "model": "ws-model"
    })
    r = client.get(f"/api/workspaces/{ws['id']}/staffs")
    by_name = {s["name"]: s for s in r.json()}
    assert by_name["reviewer"]["model"] == "ws-model"
    assert by_name["reviewer"]["inherited_from"] is None


def test_create_workspace_staff(client):
    ws = _make_ws(client)
    r = client.post(f"/api/workspaces/{ws['id']}/staffs", json={
        "name": "engineer", "adapter": "claude-code", "system_prompt": "You are an engineer"
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "engineer"
    assert body["scope_type"] == "workspace"
    assert body["scope_id"] == ws["id"]
    assert body["system_prompt"] == "You are an engineer"


def test_update_workspace_staff(client):
    ws = _make_ws(client)
    client.post(f"/api/workspaces/{ws['id']}/staffs", json={"name": "eng", "adapter": "claude-code"})
    r = client.put(f"/api/workspaces/{ws['id']}/staffs/eng", json={
        "name": "eng", "adapter": "claude-code", "model": "claude-opus-4-7"
    })
    assert r.status_code == 200
    assert r.json()["model"] == "claude-opus-4-7"


def test_delete_workspace_staff(client):
    ws = _make_ws(client)
    client.post(f"/api/workspaces/{ws['id']}/staffs", json={"name": "eng", "adapter": "claude-code"})
    r = client.delete(f"/api/workspaces/{ws['id']}/staffs/eng")
    assert r.status_code == 204


# ── Topic staff CRUD ──────────────────────────────────────────────────────────

def test_list_topic_staffs_inherits_workspace_and_global(client):
    ws = _make_ws(client)
    topic = _make_topic(client, ws["id"])
    client.post("/api/staffs", json={"name": "global-staff", "adapter": "claude-code"})
    r = client.get(f"/api/workspaces/{ws['id']}/topics/{topic['id']}/staffs")
    by_name = {s["name"]: s for s in r.json()}
    assert by_name["claude"]["inherited_from"] == "workspace"
    assert by_name["global-staff"]["inherited_from"] == "global"


def test_create_topic_staff(client):
    ws = _make_ws(client)
    topic = _make_topic(client, ws["id"])
    r = client.post(f"/api/workspaces/{ws['id']}/topics/{topic['id']}/staffs", json={
        "name": "reviewer", "adapter": "claude-code"
    })
    assert r.status_code == 201
    assert r.json()["scope_type"] == "topic"
    assert r.json()["scope_id"] == topic["id"]


def test_topic_staff_shadows_workspace(client):
    ws = _make_ws(client)
    topic = _make_topic(client, ws["id"])
    # Topic-scoped override of the default 'claude' workspace staff
    client.post(f"/api/workspaces/{ws['id']}/topics/{topic['id']}/staffs", json={
        "name": "claude", "adapter": "claude-code", "model": "claude-haiku-4-5-20251001"
    })
    r = client.get(f"/api/workspaces/{ws['id']}/topics/{topic['id']}/staffs")
    by_name = {s["name"]: s for s in r.json()}
    assert by_name["claude"]["model"] == "claude-haiku-4-5-20251001"
    assert by_name["claude"]["inherited_from"] is None
    assert by_name["claude"]["scope_type"] == "topic"


# ── Cascade resolution (via message routing) ──────────────────────────────────

def test_mention_routes_to_workspace_staff(client):
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            ws_id = c.post("/api/workspaces", json={"name": "r", "repo_url": "http://x"}).json()["id"]
            topic_id = c.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "t", "repo_ref": "main"}).json()["id"]
            import json as _json
            r = c.post(
                f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
                data={"text": "@claude hello"},
            )
            assert r.status_code == 202
            payload = _json.loads(mock_mqtt.publish.call_args.args[1])
            assert payload["adapter"] == "claude-code"
            assert payload["text"] == "hello"


def test_cascade_global_staff_resolved_via_message(client):
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            # Create a global staff with a unique name
            c.post("/api/staffs", json={"name": "reviewer", "adapter": "claude-code", "model": "gm"})
            ws_id = c.post("/api/workspaces", json={"name": "r2", "repo_url": "http://x"}).json()["id"]
            topic_id = c.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "t", "repo_ref": "main"}).json()["id"]
            import json as _json
            r = c.post(
                f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
                data={"text": "@reviewer fix this"},
            )
            assert r.status_code == 202
            payload = _json.loads(mock_mqtt.publish.call_args.args[1])
            assert payload["model"] == "gm"
            assert payload["text"] == "fix this"


# ── Session management ────────────────────────────────────────────────────────

def test_staff_session_uuid_is_deterministic(client):
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            import json as _json
            ws_id = c.post("/api/workspaces", json={"name": "r3", "repo_url": "http://x"}).json()["id"]
            topic_id = c.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "t", "repo_ref": "main"}).json()["id"]
            c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages", data={"text": "first"})
            p1 = _json.loads(mock_mqtt.publish.call_args.args[1])
            # Simulate new TestClient session (same DB) — UUID should be same
            from src.master.messages import _make_session_uuid
            expected = _make_session_uuid("topic", topic_id, "claude")
            assert p1["session_id"] == expected


def test_staff_session_is_new_on_first_send(client):
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            import json as _json
            ws_id = c.post("/api/workspaces", json={"name": "r4", "repo_url": "http://x"}).json()["id"]
            topic_id = c.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "t", "repo_ref": "main"}).json()["id"]
            c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages", data={"text": "first"})
            p1 = _json.loads(mock_mqtt.publish.call_args.args[1])
            assert p1["is_new_session"] is True
            c.post(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages", data={"text": "second"})
            p2 = _json.loads(mock_mqtt.publish.call_args.args[1])
            assert p2["is_new_session"] is False
            assert p2["session_id"] == p1["session_id"]


def test_workspace_session_scope_shares_across_topics(client):
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            import json as _json
            ws_id = c.post("/api/workspaces", json={"name": "r5", "repo_url": "http://x"}).json()["id"]
            # Update default staff to use workspace session_scope (preserve is_default)
            c.put(f"/api/workspaces/{ws_id}/staffs/claude", json={
                "name": "claude", "adapter": "claude-code", "session_scope": "workspace", "is_default": True
            })
            t1 = c.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "t1", "repo_ref": "main"}).json()["id"]
            t2 = c.post(f"/api/workspaces/{ws_id}/topics", json={"subject": "t2", "repo_ref": "main"}).json()["id"]
            c.post(f"/api/workspaces/{ws_id}/topics/{t1}/messages", data={"text": "msg1"})
            p1 = _json.loads(mock_mqtt.publish.call_args.args[1])
            c.post(f"/api/workspaces/{ws_id}/topics/{t2}/messages", data={"text": "msg2"})
            p2 = _json.loads(mock_mqtt.publish.call_args.args[1])
            # Same workspace-scoped session UUID for both topics
            assert p1["session_id"] == p2["session_id"]
