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
    ws = c.post("/api/workspaces", json={"name": "repo", "repo_url": "https://github.com/x/y"}).json()
    topic = c.post(f"/api/workspaces/{ws['id']}/topics", json={"subject": "Fix bug"}).json()
    return ws["id"], topic["id"]


def send(client, ws_id, topic_id, text="Hello", agent="claude"):
    c, _ = client
    return c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
        data={"text": text, "agent_name": agent},
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
    msgs = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages").json()
    assert any(m["sender"] == "user" and m["text"] == "Remember this" for m in msgs)


def test_send_unknown_workspace(client, workspace_topic):
    _, topic_id = workspace_topic
    c, _ = client
    r = c.post(f"/api/workspaces/no-such/topics/{topic_id}/messages", data={"text": "hi"})
    assert r.status_code == 404


def test_send_unknown_topic(client, workspace_topic):
    ws_id, _ = workspace_topic
    c, _ = client
    r = c.post(f"/api/workspaces/{ws_id}/topics/no-such/messages", data={"text": "hi"})
    assert r.status_code == 404


def test_send_unknown_agent(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = send(client, ws_id, topic_id, agent="no-such-agent")
    assert r.status_code == 404


# --- list messages ---

def test_list_messages_empty(client, workspace_topic):
    c, _ = client
    ws_id, topic_id = workspace_topic
    r = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages")
    assert r.status_code == 200
    assert r.json() == []


def test_list_messages_ordered(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    send(client, ws_id, topic_id, text="First")
    send(client, ws_id, topic_id, text="Second")
    c, _ = client
    msgs = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/messages").json()
    assert [m["text"] for m in msgs] == ["First", "Second"]


def test_list_messages_unknown_workspace(client):
    c, _ = client
    r = c.get("/api/workspaces/no-such/topics/no-such/messages")
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


# --- @mention routing ---

def test_mention_routes_to_named_agent(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    c, mock_mqtt = client
    send(client, ws_id, topic_id, text="@codex write tests")
    import json
    payload = json.loads(mock_mqtt.publish.call_args.args[1])
    assert payload["adapter"] == "codex"
    assert payload["text"] == "write tests"


def test_mention_strips_prefix_from_prompt(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    c, mock_mqtt = client
    send(client, ws_id, topic_id, text="@claude explain this")
    import json
    payload = json.loads(mock_mqtt.publish.call_args.args[1])
    assert payload["text"] == "explain this"


def test_mention_unknown_agent_returns_404(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = send(client, ws_id, topic_id, text="@nobody do something")
    assert r.status_code == 404


def test_no_mention_uses_default_agent(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    c, mock_mqtt = client
    send(client, ws_id, topic_id, text="plain text no mention")
    import json
    payload = json.loads(mock_mqtt.publish.call_args.args[1])
    assert payload["adapter"] == "claude-code"
    assert payload["text"] == "plain text no mention"
