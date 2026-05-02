from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        from src.master.main import app
        with TestClient(app) as c:
            yield c, mock_mqtt


@pytest.fixture()
def workspace_topic(client):
    c, _ = client
    ws = c.post("/workspaces", json={"name": "repo", "repo_url": "https://github.com/x/y"}).json()
    topic = c.post(f"/workspaces/{ws['id']}/topics", json={"subject": "Fix bug"}).json()
    return ws["id"], topic["id"]


def send(client, ws_id, topic_id, text="Hello", agent="claude"):
    c, _ = client
    return c.post(
        f"/workspaces/{ws_id}/topics/{topic_id}/messages",
        json={"text": text, "agent_name": agent},
    )


# --- send message ---

def test_send_returns_202(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = send(client, ws_id, topic_id)
    assert r.status_code == 202


def test_send_returns_message_id_and_queued(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    body = send(client, ws_id, topic_id).json()
    assert "message_id" in body
    assert body["status"] == "queued"


def test_send_publishes_mqtt(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    c, mock_mqtt = client
    send(client, ws_id, topic_id, text="Do it")
    assert mock_mqtt.publish.called
    call_args = mock_mqtt.publish.call_args
    topic = call_args.args[0]
    assert topic_id in topic
    assert "prompt" in topic
    import json
    payload = json.loads(call_args.args[1])
    assert payload["text"] == "Do it"
    assert payload["worktree"].startswith("/workspace/worktrees/")


def test_send_saves_user_message(client, workspace_topic):
    c, _ = client
    ws_id, topic_id = workspace_topic
    send(client, ws_id, topic_id, text="Remember this")
    msgs = c.get(f"/workspaces/{ws_id}/topics/{topic_id}/messages").json()
    assert any(m["sender"] == "user" and m["text"] == "Remember this" for m in msgs)


def test_send_unknown_workspace(client, workspace_topic):
    _, topic_id = workspace_topic
    c, _ = client
    r = c.post(f"/workspaces/no-such/topics/{topic_id}/messages", json={"text": "hi"})
    assert r.status_code == 404


def test_send_unknown_topic(client, workspace_topic):
    ws_id, _ = workspace_topic
    c, _ = client
    r = c.post(f"/workspaces/{ws_id}/topics/no-such/messages", json={"text": "hi"})
    assert r.status_code == 404


def test_send_unknown_agent(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = send(client, ws_id, topic_id, agent="no-such-agent")
    assert r.status_code == 404


# --- list messages ---

def test_list_messages_empty(client, workspace_topic):
    c, _ = client
    ws_id, topic_id = workspace_topic
    r = c.get(f"/workspaces/{ws_id}/topics/{topic_id}/messages")
    assert r.status_code == 200
    assert r.json() == []


def test_list_messages_ordered(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    send(client, ws_id, topic_id, text="First")
    send(client, ws_id, topic_id, text="Second")
    c, _ = client
    msgs = c.get(f"/workspaces/{ws_id}/topics/{topic_id}/messages").json()
    assert [m["text"] for m in msgs] == ["First", "Second"]


def test_list_messages_unknown_workspace(client):
    c, _ = client
    r = c.get("/workspaces/no-such/topics/no-such/messages")
    assert r.status_code == 404


# --- session created on first send ---

def test_send_creates_session(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    c, mock_mqtt = client
    send(client, ws_id, topic_id, text="first turn")
    import json
    payload = json.loads(mock_mqtt.publish.call_args.args[1])
    # first turn: session_id is None (no LLM session yet)
    assert payload["session_id"] is None
