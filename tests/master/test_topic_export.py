from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.master.main import app
from src.master.topic_export import (
    _slug_filename,
    _slugify,
    render_jsonl,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def workspace_topic(client):
    ws = client.post("/api/workspaces", json={"name": "My Repo", "repo_url": "https://github.com/x/y"}).json()
    topic = client.post(
        f"/api/workspaces/{ws['id']}/topics",
        json={"subject": "Fix the bug", "repo_ref": "main"},
    ).json()
    return ws["id"], topic["id"]


def _insert_message(sender, text, topic_id, agent_name=None, transcript=None):
    """Insert a message row directly into the test DB, bypassing the dispatch queue."""
    data_dir = os.environ.get("MASTER_DATA_DIR", "/tmp")
    db_path = str(Path(data_dir) / "master_data.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    mid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO messages (id, topic_id, sender, agent_name, text, transcript, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (mid, topic_id, sender, agent_name, text, transcript),
    )
    conn.commit()
    conn.close()
    return mid


def _parse_jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.strip().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Unit tests: _slugify
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert _slugify("Fix the bug") == "fix-the-bug"


def test_slugify_special_chars():
    assert _slugify("Add feat: new/thing!") == "add-feat-newthing"


def test_slugify_empty_falls_back():
    assert _slugify("!@#$%") == ""


def test_slugify_truncates():
    assert len(_slugify("a" * 100)) <= 64


# ---------------------------------------------------------------------------
# Unit tests: render_jsonl
# ---------------------------------------------------------------------------

def test_render_jsonl_empty():
    assert render_jsonl([]) == ""


def test_render_jsonl_user_message():
    msgs = [{"sender": "user", "text": "Hello", "created_at": "2026-05-09T10:00:00Z",
             "agent_name": None, "transcript": None, "attachments": None}]
    lines = _parse_jsonl(render_jsonl(msgs))
    assert len(lines) == 1
    assert lines[0]["type"] == "user"
    assert lines[0]["timestamp"] == "2026-05-09T10:00:00Z"
    assert lines[0]["text"] == "Hello"
    assert "transcript" not in lines[0]


def test_render_jsonl_agent_message_type():
    msgs = [{"sender": "agent", "agent_name": "claude", "text": "Done.",
             "created_at": "2026-05-09T10:01:00Z", "transcript": None, "attachments": None}]
    lines = _parse_jsonl(render_jsonl(msgs))
    assert lines[0]["type"] == "agent"
    assert lines[0]["agent_name"] == "claude"
    assert lines[0]["timestamp"] == "2026-05-09T10:01:00Z"


def test_render_jsonl_transcript_parsed_as_json():
    events = [
        {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"type": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
    ]
    msgs = [{"sender": "agent", "agent_name": "claude", "text": "hi",
             "created_at": "2026-05-09T10:01:00Z",
             "transcript": json.dumps(events), "attachments": None}]
    lines = _parse_jsonl(render_jsonl(msgs))
    # transcript must be the parsed array, not a string
    assert isinstance(lines[0]["transcript"], list)
    assert lines[0]["transcript"][0]["type"] == "assistant"
    assert lines[0]["transcript"][1]["type"] == "user"


def test_render_jsonl_transcript_all_event_types_preserved():
    events = [
        {"type": "assistant", "content": [
            {"type": "thinking", "thinking": "Let me think."},
            {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {"command": "ls"}},
        ]},
        {"type": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "main.py"},
        ]},
    ]
    msgs = [{"sender": "agent", "agent_name": "claude", "text": "Done.",
             "created_at": "2026-05-09T10:01:00Z",
             "transcript": json.dumps(events), "attachments": None}]
    lines = _parse_jsonl(render_jsonl(msgs))
    transcript = lines[0]["transcript"]
    # All events preserved verbatim — no filtering
    assert len(transcript) == 2
    assert transcript[0]["content"][0]["type"] == "thinking"
    assert transcript[0]["content"][1]["type"] == "tool_use"
    assert transcript[1]["content"][0]["type"] == "tool_result"


def test_render_jsonl_attachments_included():
    atts = [{"id": "att-1", "filename": "photo.png", "mime_type": "image/png"}]
    msgs = [{"sender": "user", "text": "See attached", "created_at": "2026-05-09T10:00:00Z",
             "agent_name": None, "transcript": None, "attachments": atts}]
    lines = _parse_jsonl(render_jsonl(msgs))
    assert lines[0]["attachments"][0]["filename"] == "photo.png"
    assert lines[0]["attachments"][0]["id"] == "att-1"


def test_render_jsonl_no_agent_name_omitted():
    msgs = [{"sender": "agent", "agent_name": None, "text": "hi",
             "created_at": "2026-05-09T10:01:00Z", "transcript": None, "attachments": None}]
    lines = _parse_jsonl(render_jsonl(msgs))
    assert "agent_name" not in lines[0]


def test_render_jsonl_invalid_transcript_kept_as_string():
    msgs = [{"sender": "agent", "agent_name": "claude", "text": ".",
             "created_at": "2026-05-09T10:01:00Z",
             "transcript": "not valid json", "attachments": None}]
    lines = _parse_jsonl(render_jsonl(msgs))
    assert lines[0]["transcript"] == "not valid json"


def test_render_jsonl_multiple_messages_one_per_line():
    msgs = [
        {"sender": "user", "text": "hi", "created_at": "2026-05-09T10:00:00Z",
         "agent_name": None, "transcript": None, "attachments": None},
        {"sender": "agent", "agent_name": "claude", "text": "hello",
         "created_at": "2026-05-09T10:01:00Z", "transcript": None, "attachments": None},
    ]
    output = render_jsonl(msgs)
    lines = [l for l in output.splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "user"
    assert json.loads(lines[1])["type"] == "agent"


def test_render_jsonl_each_line_valid_json():
    events = [{"type": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    msgs = [
        {"sender": "user", "text": "q", "created_at": "2026-05-09T10:00:00Z",
         "agent_name": None, "transcript": None, "attachments": None},
        {"sender": "agent", "agent_name": "claude", "text": "a",
         "created_at": "2026-05-09T10:01:00Z",
         "transcript": json.dumps(events), "attachments": None},
    ]
    for line in render_jsonl(msgs).splitlines():
        if line.strip():
            json.loads(line)  # must not raise


# ---------------------------------------------------------------------------
# Integration tests: HTTP endpoint
# ---------------------------------------------------------------------------

def test_export_returns_200(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert r.status_code == 200


def test_export_content_type(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert "application/x-ndjson" in r.headers["content-type"]


def test_export_content_disposition_jsonl(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".jsonl" in cd


def test_export_empty_topic_returns_empty_body(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert r.status_code == 200
    assert r.text.strip() == ""


def test_export_user_message_shape(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    _insert_message("user", "What should I do?", topic_id)
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    lines = _parse_jsonl(r.text)
    assert len(lines) == 1
    assert lines[0]["type"] == "user"
    assert lines[0]["text"] == "What should I do?"
    assert "timestamp" in lines[0]


def test_export_agent_message_with_full_transcript(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    events = [
        {"type": "assistant", "content": [
            {"type": "thinking", "thinking": "Let me think."},
            {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {"command": "ls"}},
        ]},
        {"type": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "main.py"},
        ]},
    ]
    _insert_message("agent", "Done.", topic_id, agent_name="claude",
                    transcript=json.dumps(events))
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    lines = _parse_jsonl(r.text)
    assert lines[0]["type"] == "agent"
    assert lines[0]["agent_name"] == "claude"
    transcript = lines[0]["transcript"]
    # All raw events present — nothing stripped
    assert len(transcript) == 2
    assert transcript[0]["content"][0]["type"] == "thinking"
    assert transcript[0]["content"][1]["name"] == "bash"
    assert transcript[1]["content"][0]["content"] == "main.py"


def test_export_transcript_is_parsed_not_string(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    _insert_message("agent", "ok", topic_id, agent_name="claude",
                    transcript=json.dumps([{"type": "assistant", "content": []}]))
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    obj = _parse_jsonl(r.text)[0]
    assert isinstance(obj["transcript"], list)


def test_export_unknown_workspace(client, workspace_topic):
    _, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/no-such/topics/{topic_id}/export")
    assert r.status_code == 404


def test_export_unknown_topic(client, workspace_topic):
    ws_id, _ = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/no-such/export")
    assert r.status_code == 404


def test_export_archived_topic(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    client.delete(f"/api/workspaces/{ws_id}/topics/{topic_id}")
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert r.status_code == 200


def test_export_filename_slug(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    cd = r.headers.get("content-disposition", "")
    assert "my-repo" in cd
    assert "fix-the-bug" in cd
    assert ".jsonl" in cd


def test_export_message_order(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    _insert_message("user", "first", topic_id)
    _insert_message("agent", "second", topic_id, agent_name="claude")
    lines = _parse_jsonl(client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export").text)
    assert lines[0]["text"] == "first"
    assert lines[1]["text"] == "second"
