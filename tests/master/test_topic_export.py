from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.master.main import app
from src.master.topic_export import (
    _fenced,
    _parse_transcript,
    _slug_filename,
    _slugify,
    render_markdown,
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
# Unit tests: _fenced
# ---------------------------------------------------------------------------

def test_fenced_plain():
    result = _fenced("hello world")
    assert result.startswith("```")
    assert "hello world" in result
    assert result.endswith("```")


def test_fenced_with_lang():
    result = _fenced('{"a": 1}', "json")
    assert result.startswith("```json")


def test_fenced_escapes_backticks():
    content = "some ```code``` here"
    result = _fenced(content)
    first_line = result.split("\n")[0]
    assert len(first_line) >= 4  # outer fence must be longer than inner run of 3


# ---------------------------------------------------------------------------
# Unit tests: _parse_transcript
# ---------------------------------------------------------------------------

def test_parse_transcript_none():
    thinking, tools = _parse_transcript(None)
    assert thinking == []
    assert tools == []


def test_parse_transcript_empty_list():
    thinking, tools = _parse_transcript("[]")
    assert thinking == []
    assert tools == []


def test_parse_transcript_invalid_json():
    thinking, tools = _parse_transcript("not json")
    assert thinking == []
    assert tools == []


def test_parse_transcript_thinking_block():
    events = [
        {"type": "assistant", "content": [
            {"type": "thinking", "thinking": "I should fix this."},
        ]}
    ]
    thinking, tools = _parse_transcript(json.dumps(events))
    assert len(thinking) == 1
    assert thinking[0].text == "I should fix this."
    assert tools == []


def test_parse_transcript_tool_use_and_result():
    events = [
        {"type": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {"command": "ls"}},
        ]},
        {"type": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "file1.py\nfile2.py"},
        ]},
    ]
    thinking, tools = _parse_transcript(json.dumps(events))
    assert len(tools) == 1
    assert tools[0].name == "bash"
    assert tools[0].input == {"command": "ls"}
    assert tools[0].result == "file1.py\nfile2.py"


def test_parse_transcript_tool_use_without_result():
    events = [
        {"type": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {"command": "ls"}},
        ]},
    ]
    _, tools = _parse_transcript(json.dumps(events))
    assert len(tools) == 1
    assert tools[0].result is None


def test_parse_transcript_tool_result_content_list():
    events = [
        {"type": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "read", "input": {}},
        ]},
        {"type": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": [
                {"type": "text", "text": "line1"},
                {"type": "text", "text": "line2"},
            ]},
        ]},
    ]
    _, tools = _parse_transcript(json.dumps(events))
    assert tools[0].result == "line1\nline2"


def test_parse_transcript_skips_noise_events():
    events = [
        {"type": "task_progress", "description": "working"},
        {"type": "task_started", "description": "started"},
        {"type": "retry_notice"},
        {"type": "agent_result"},
        {"type": "assistant", "content": [
            {"type": "thinking", "thinking": "real content"},
        ]},
    ]
    thinking, tools = _parse_transcript(json.dumps(events))
    assert len(thinking) == 1
    assert tools == []


def test_parse_transcript_multiple_thinking_blocks():
    events = [
        {"type": "assistant", "content": [
            {"type": "thinking", "thinking": "first thought"},
            {"type": "thinking", "thinking": "second thought"},
        ]},
    ]
    thinking, _ = _parse_transcript(json.dumps(events))
    assert len(thinking) == 2
    assert thinking[0].text == "first thought"
    assert thinking[1].text == "second thought"


# ---------------------------------------------------------------------------
# Unit tests: render_markdown
# ---------------------------------------------------------------------------

def test_render_markdown_header():
    md = render_markdown("My Workspace", "My Topic", "topic-id-1", [])
    assert "# Topic: My Topic" in md
    assert "Workspace: My Workspace" in md
    assert "Exported:" in md


def test_render_markdown_empty_topic():
    md = render_markdown("ws", "t", "tid", [])
    assert "# Topic: t" in md
    assert "---" in md


def test_render_markdown_user_message():
    msgs = [{"sender": "user", "text": "Hello agent", "created_at": "2026-05-09T10:00:00Z",
             "agent_name": None, "transcript": None, "attachments": []}]
    md = render_markdown("ws", "topic", "tid", msgs)
    assert "## User — 2026-05-09T10:00:00Z" in md
    assert "Hello agent" in md


def test_render_markdown_agent_message_no_transcript():
    msgs = [{"sender": "agent", "agent_name": "claude", "text": "I fixed it.",
             "created_at": "2026-05-09T10:01:00Z", "transcript": None, "attachments": []}]
    md = render_markdown("ws", "topic", "tid", msgs)
    assert "## Agent (claude) — 2026-05-09T10:01:00Z" in md
    assert "I fixed it." in md


def test_render_markdown_agent_message_with_thinking():
    transcript = json.dumps([
        {"type": "assistant", "content": [
            {"type": "thinking", "thinking": "My inner thought."},
        ]},
    ])
    msgs = [{"sender": "agent", "agent_name": "claude", "text": "Done.",
             "created_at": "2026-05-09T10:01:00Z", "transcript": transcript, "attachments": []}]
    md = render_markdown("ws", "topic", "tid", msgs)
    assert "<details>" in md
    assert "<summary>Thinking</summary>" in md
    assert "My inner thought." in md


def test_render_markdown_agent_message_with_tool():
    transcript = json.dumps([
        {"type": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {"command": "echo hi"}},
        ]},
        {"type": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "hi"},
        ]},
    ])
    msgs = [{"sender": "agent", "agent_name": "claude", "text": "Done.",
             "created_at": "2026-05-09T10:01:00Z", "transcript": transcript, "attachments": []}]
    md = render_markdown("ws", "topic", "tid", msgs)
    assert "<summary>Tool: bash</summary>" in md
    assert "**Input:**" in md
    assert "echo hi" in md
    assert "**Output:**" in md
    assert "hi" in md


def test_render_markdown_tool_no_result():
    transcript = json.dumps([
        {"type": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "read", "input": {}},
        ]},
    ])
    msgs = [{"sender": "agent", "agent_name": "claude", "text": ".",
             "created_at": "2026-05-09T10:01:00Z", "transcript": transcript, "attachments": []}]
    md = render_markdown("ws", "topic", "tid", msgs)
    assert "_(no result captured)_" in md


def test_render_markdown_attachment_links():
    msgs = [{"sender": "user", "text": "See attached", "created_at": "2026-05-09T10:00:00Z",
             "agent_name": None, "transcript": None,
             "attachments": [{"id": "att-abc", "filename": "photo.png"}]}]
    md = render_markdown("ws", "topic", "tid", msgs)
    assert "[photo.png](/attachments/att-abc/download)" in md


def test_render_markdown_fallback_subject():
    md = render_markdown("ws", "", "my-topic-id", [])
    assert "# Topic: my-topic-id" in md


# ---------------------------------------------------------------------------
# Integration tests: HTTP endpoint
# ---------------------------------------------------------------------------

def test_export_returns_200(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export?format=md")
    assert r.status_code == 200


def test_export_content_type(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert "text/markdown" in r.headers["content-type"]


def test_export_content_disposition(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".md" in cd


def test_export_contains_topic_subject(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert "Fix the bug" in r.text


def test_export_contains_workspace_name(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert "My Repo" in r.text


def test_export_unknown_workspace(client, workspace_topic):
    _, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/no-such/topics/{topic_id}/export")
    assert r.status_code == 404


def test_export_unknown_topic(client, workspace_topic):
    ws_id, _ = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/no-such/export")
    assert r.status_code == 404


def test_export_unsupported_format(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export?format=pdf")
    assert r.status_code == 422


def test_export_empty_topic(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert r.status_code == 200
    assert "# Topic:" in r.text


def test_export_includes_messages(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    _insert_message("user", "What should I do?", topic_id)
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert "What should I do?" in r.text


def test_export_agent_message_with_transcript(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    transcript = json.dumps([
        {"type": "assistant", "content": [
            {"type": "thinking", "thinking": "Let me think..."},
            {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {"command": "ls"}},
        ]},
        {"type": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "main.py"},
        ]},
    ])
    _insert_message("agent", "Done.", topic_id, agent_name="claude", transcript=transcript)
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    body = r.text
    assert "Let me think..." in body
    assert "bash" in body
    assert "main.py" in body


def test_export_archived_topic(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    # Archive via DELETE (sets archived_at, does not remove the row)
    client.delete(f"/api/workspaces/{ws_id}/topics/{topic_id}")
    # Export must still work — archived topics are readable
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    assert r.status_code == 200


def test_export_filename_slug(client, workspace_topic):
    ws_id, topic_id = workspace_topic
    r = client.get(f"/api/workspaces/{ws_id}/topics/{topic_id}/export")
    cd = r.headers.get("content-disposition", "")
    assert "my-repo" in cd
    assert "fix-the-bug" in cd
