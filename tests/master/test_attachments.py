from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.storage import LocalAttachmentStore


# --- LocalAttachmentStore unit tests ---

def test_local_store_put_and_get(tmp_path):
    store = LocalAttachmentStore(str(tmp_path))
    data = b"hello attachment"
    uri = store.put("att-001", "hello.txt", data)
    assert uri.startswith("file://")
    result = store.get(uri)
    assert result == data


def test_local_store_put_creates_directory(tmp_path):
    store = LocalAttachmentStore(str(tmp_path))
    uri = store.put("att-002", "data.bin", b"\x00\x01\x02")
    assert uri.startswith("file://")
    import pathlib
    path = pathlib.Path(uri.removeprefix("file://"))
    assert path.exists()
    assert path.name == "data.bin"


def test_local_store_delete(tmp_path):
    store = LocalAttachmentStore(str(tmp_path))
    uri = store.put("att-003", "del.txt", b"bye")
    import pathlib
    path = pathlib.Path(uri.removeprefix("file://"))
    assert path.exists()
    store.delete(uri)
    assert not path.exists()


def test_local_store_delete_nonexistent(tmp_path):
    store = LocalAttachmentStore(str(tmp_path))
    # Should not raise
    store.delete("file:///nonexistent/path/file.txt")


# --- API tests fixture ---

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
    topic = c.post(f"/api/workspaces/{ws['id']}/topics", json={"subject": "Fix bug", "repo_ref": "main"}).json()
    return ws["id"], topic["id"]


# --- GET /attachments list ---

def test_list_attachments_empty(client, workspace_topic):
    c, _ = client
    ws_id, topic_id = workspace_topic
    r = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/attachments")
    assert r.status_code == 200
    assert r.json() == []


# --- POST messages with multipart (text only, no files) ---

def test_send_message_multipart_text_only(client, workspace_topic):
    c, mock_mqtt = client
    ws_id, topic_id = workspace_topic
    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
        data={"text": "Hello multipart"},
    )
    assert r.status_code == 202
    body = r.json()
    assert "message_id" in body
    assert body["status"] == "queued"


def test_send_message_multipart_text_only_no_attachments(client, workspace_topic):
    c, mock_mqtt = client
    ws_id, topic_id = workspace_topic
    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
        data={"text": "No files"},
    )
    assert r.status_code == 202
    import json
    payload = json.loads(mock_mqtt.publish.call_args.args[1])
    assert payload["attachments"] == []


# --- POST messages with a file ---

def test_send_message_with_file_creates_attachment(client, workspace_topic, tmp_path):
    c, mock_mqtt = client
    ws_id, topic_id = workspace_topic
    file_content = b"test file content"
    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
        data={"text": "Here is a file"},
        files=[("files", ("test.txt", io.BytesIO(file_content), "text/plain"))],
    )
    assert r.status_code == 202
    msg_id = r.json()["message_id"]

    # Check attachment row exists via list endpoint
    atts = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/attachments").json()
    assert len(atts) == 1
    att = atts[0]
    assert att["filename"] == "test.txt"
    assert att["mime_type"] == "text/plain"
    assert att["size_bytes"] == len(file_content)
    assert att["message_id"] == msg_id
    assert att["direction"] == "inbound"


def test_send_message_with_file_mqtt_payload_has_attachments(client, workspace_topic):
    c, mock_mqtt = client
    ws_id, topic_id = workspace_topic
    r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
        data={"text": "With file"},
        files=[("files", ("doc.pdf", io.BytesIO(b"pdf content"), "application/pdf"))],
    )
    assert r.status_code == 202
    import json
    payload = json.loads(mock_mqtt.publish.call_args.args[1])
    assert len(payload["attachments"]) == 1
    att = payload["attachments"][0]
    assert att["filename"] == "doc.pdf"
    assert att["mime_type"] == "application/pdf"
    assert "id" in att


# --- GET /attachments/{id}/download ---

def test_download_attachment_returns_bytes(client, workspace_topic):
    c, _ = client
    ws_id, topic_id = workspace_topic
    file_content = b"downloadable content"
    # Upload via send
    send_r = c.post(
        f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
        data={"text": "download test"},
        files=[("files", ("down.txt", io.BytesIO(file_content), "text/plain"))],
    )
    assert send_r.status_code == 202

    atts = c.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/attachments").json()
    att_id = atts[0]["id"]

    dl = c.get(f"/api/attachments/{att_id}/download")
    assert dl.status_code == 200
    assert dl.content == file_content
    assert dl.headers["content-type"].startswith("text/plain")
    assert "down.txt" in dl.headers.get("content-disposition", "")


def test_download_attachment_not_found(client):
    c, _ = client
    r = c.get("/api/attachments/nonexistent-id/download")
    assert r.status_code == 404
    assert r.json()["detail"] == "attachment not found"


# --- File size limit ---

def test_send_message_file_exceeds_limit(client, workspace_topic, monkeypatch):
    c, _ = client
    ws_id, topic_id = workspace_topic
    # monkeypatch the setting on the app state
    original_max = c.app.state.settings.attachment_max_size_mb
    # Create a tiny size limit (1 byte) by patching the property
    from unittest.mock import PropertyMock
    with patch.object(type(c.app.state.settings), "attachment_max_size_bytes", new_callable=PropertyMock, return_value=1):
        r = c.post(
            f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
            data={"text": "big file"},
            files=[("files", ("big.bin", io.BytesIO(b"too big content"), "application/octet-stream"))],
        )
    assert r.status_code == 413
