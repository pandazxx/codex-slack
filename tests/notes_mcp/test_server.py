"""Tests for src.notes_mcp.server — workspace and topic note MCP tools."""
from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_server_module(env_overrides: dict[str, str]) -> Any:
    """Import src.notes_mcp.server in a fresh module context with the given env."""
    for key in list(sys.modules):
        if "notes_mcp" in key:
            del sys.modules[key]
    with patch.dict("os.environ", env_overrides, clear=False):
        mod = importlib.import_module("src.notes_mcp.server")
    return mod


def _make_http_response(json_data: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = resp
    return resp


# ---------------------------------------------------------------------------
# Module-level fixture: load server once with known env (no TOPIC_ID)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def server_mod(monkeypatch):
    monkeypatch.setenv("MASTER_URL", "http://master:8080")
    monkeypatch.setenv("WORKSPACE_ID", "ws1")
    monkeypatch.delenv("TOPIC_ID", raising=False)
    mod = _fresh_server_module({
        "MASTER_URL": "http://master:8080",
        "WORKSPACE_ID": "ws1",
    })
    yield mod


# ---------------------------------------------------------------------------
# 1. list_workspace_notes — no tag filter
# ---------------------------------------------------------------------------

def test_list_workspace_notes_no_tag(server_mod):
    notes = [
        {"key": "alpha", "value": "v1", "tags": ["memory"]},
        {"key": "beta",  "value": "v2", "tags": ["context"]},
    ]
    mock_resp = _make_http_response(notes)
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.list_workspace_notes()

    assert result == notes
    mock_client.get.assert_called_once_with("http://master:8080/api/workspaces/ws1/notes")


# ---------------------------------------------------------------------------
# 2. list_workspace_notes — tag filter applied client-side
# ---------------------------------------------------------------------------

def test_list_workspace_notes_with_tag(server_mod):
    notes = [
        {"key": "alpha", "value": "v1", "tags": ["memory"]},
        {"key": "beta",  "value": "v2", "tags": ["context"]},
    ]
    mock_resp = _make_http_response(notes)
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.list_workspace_notes(tag="memory")

    assert result == [{"key": "alpha", "value": "v1", "tags": ["memory"]}]


# ---------------------------------------------------------------------------
# 3. get_workspace_note
# ---------------------------------------------------------------------------

def test_get_workspace_note(server_mod):
    note = {"key": "alpha", "value": "hello", "tags": []}
    mock_resp = _make_http_response(note)
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.get_workspace_note("alpha")

    assert result == note
    mock_client.get.assert_called_once_with("http://master:8080/api/workspaces/ws1/notes/alpha")


# ---------------------------------------------------------------------------
# 4. create_workspace_note
# ---------------------------------------------------------------------------

def test_create_workspace_note(server_mod):
    created = {"key": "newkey", "value": "newval", "tags": ["x"]}
    mock_resp = _make_http_response(created, status_code=201)
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.create_workspace_note("newkey", "newval", tags=["x"])

    assert result == created
    mock_client.post.assert_called_once_with(
        "http://master:8080/api/workspaces/ws1/notes",
        json={"key": "newkey", "value": "newval", "tags": ["x"]},
    )


# ---------------------------------------------------------------------------
# 5. update_workspace_note — value only
# ---------------------------------------------------------------------------

def test_update_workspace_note(server_mod):
    updated = {"key": "alpha", "value": "updated", "tags": []}
    mock_resp = _make_http_response(updated)
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.patch.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.update_workspace_note("alpha", value="updated")

    assert result == updated
    mock_client.patch.assert_called_once_with(
        "http://master:8080/api/workspaces/ws1/notes/alpha",
        json={"value": "updated"},
    )


# ---------------------------------------------------------------------------
# 6. delete_workspace_note
# ---------------------------------------------------------------------------

def test_delete_workspace_note(server_mod):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = mock_resp
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.delete.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.delete_workspace_note("alpha")

    assert result == "Deleted workspace note: alpha"
    mock_client.delete.assert_called_once_with("http://master:8080/api/workspaces/ws1/notes/alpha")


# ---------------------------------------------------------------------------
# 7. topic tools always registered (topic_id is an explicit argument, not env-gated)
# ---------------------------------------------------------------------------

def test_topic_tools_always_registered():
    for env in [
        {"MASTER_URL": "http://master:8080", "WORKSPACE_ID": "ws1"},
        {"MASTER_URL": "http://master:8080", "WORKSPACE_ID": "ws1", "TOPIC_ID": "t42"},
    ]:
        mod = _fresh_server_module(env)
        tool_names = {t.name for t in mod.mcp._tool_manager.list_tools()}
        assert "list_topic_notes" in tool_names
        assert "create_topic_note" in tool_names
        assert "update_topic_note" in tool_names
        assert "delete_topic_note" in tool_names


# ---------------------------------------------------------------------------
# 8. list_topic_notes — explicit topic_id argument
# ---------------------------------------------------------------------------

def test_list_topic_notes(server_mod):
    notes = [{"key": "tn1", "value": "tv1", "tags": ["memory"]}]
    mock_resp = _make_http_response(notes)
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.list_topic_notes("t42")

    assert result == notes
    mock_client.get.assert_called_once_with(
        "http://master:8080/api/workspaces/ws1/topics/t42/notes"
    )


# ---------------------------------------------------------------------------
# 9. create_topic_note
# ---------------------------------------------------------------------------

def test_create_topic_note(server_mod):
    created = {"key": "tk1", "value": "tv1", "tags": []}
    mock_resp = _make_http_response(created, status_code=201)
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.create_topic_note("t42", "tk1", "tv1")

    assert result == created
    mock_client.post.assert_called_once_with(
        "http://master:8080/api/workspaces/ws1/topics/t42/notes",
        json={"key": "tk1", "value": "tv1", "tags": []},
    )


# ---------------------------------------------------------------------------
# 10. delete_topic_note
# ---------------------------------------------------------------------------

def test_delete_topic_note(server_mod):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = mock_resp
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.delete.return_value = mock_resp

    with patch.object(server_mod, "_client", return_value=mock_client):
        result = server_mod.delete_topic_note("t42", "tk1")

    assert result == "Deleted topic note: tk1"
    mock_client.delete.assert_called_once_with(
        "http://master:8080/api/workspaces/ws1/topics/t42/notes/tk1"
    )
