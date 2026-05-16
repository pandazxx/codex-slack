"""Tests for the notes CRUD API and render_template integration.

Test plan: docs/test-plans/notes.md
Design doc: docs/design/notes.md

Coverage matrix:
  1.  CRUD round-trip — workspace-scoped notes
  2.  CRUD round-trip — topic-scoped notes
  3.  Duplicate key on POST → 409
  4.  GET/PATCH/DELETE non-existent key → 404
  5.  PATCH with key field present → 422 (NotePatch has extra="forbid")
  6.  Tag filtering in render_template: note appears in matching tag but not in unrelated tag
  7.  notelist output is sorted by key; two notes produce two "key: value" lines
  8.  Empty tag match → empty string (not literal marker)
  9.  {t:note:notelist:memory} → empty string + WARNING (v1 only supports ws)
 10.  {variable} placeholders coexist with note markers in one pass
 11.  render_template with no db_path/workspace_id → note markers → empty string + WARNING
 12.  Unknown {variable} → left literal + WARNING
 13.  Staff system_prompt with note marker → injected value appears in MQTT payload
"""
from __future__ import annotations

import json
import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.main import app
from src.master.event_dispatcher import render_template
from src.master.db import get_connection, init_db


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI TestClient with an isolated on-disk SQLite DB."""
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client_mqtt(tmp_path, monkeypatch):
    """TestClient with isolated DB and captured mock MQTT. Yields (client, mock_mqtt)."""
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            yield c, mock_mqtt


@pytest.fixture()
def workspace_id(client):
    r = client.post(
        "/api/workspaces",
        json={"name": "test-workspace", "repo_url": "https://github.com/x/y"},
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture()
def topic_id(client, workspace_id):
    r = client.post(
        f"/api/workspaces/{workspace_id}/topics",
        json={"subject": "test topic", "repo_ref": "main"},
    )
    assert r.status_code == 201
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws_notes_url(workspace_id: str, key: str | None = None) -> str:
    base = f"/api/workspaces/{workspace_id}/notes"
    return f"{base}/{key}" if key else base


def _topic_notes_url(workspace_id: str, topic_id: str, key: str | None = None) -> str:
    base = f"/api/workspaces/{workspace_id}/topics/{topic_id}/notes"
    return f"{base}/{key}" if key else base


# ---------------------------------------------------------------------------
# 1. CRUD round-trip — workspace-scoped notes
# ---------------------------------------------------------------------------


def test_ws_note_create(client, workspace_id):
    r = client.post(
        _ws_notes_url(workspace_id),
        json={"key": "goal", "value": "ship fast", "tags": ["memory"]},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["key"] == "goal"
    assert body["value"] == "ship fast"
    assert body["tags"] == ["memory"]
    assert body["scope_type"] == "workspace"
    assert body["scope_id"] == workspace_id
    assert body["created_at"]
    assert body["updated_at"]


def test_ws_note_get(client, workspace_id):
    client.post(_ws_notes_url(workspace_id), json={"key": "k1", "value": "v1", "tags": []})
    r = client.get(_ws_notes_url(workspace_id, "k1"))
    assert r.status_code == 200
    assert r.json()["value"] == "v1"


def test_ws_note_list(client, workspace_id):
    client.post(_ws_notes_url(workspace_id), json={"key": "alpha", "value": "a", "tags": []})
    client.post(_ws_notes_url(workspace_id), json={"key": "beta", "value": "b", "tags": []})
    r = client.get(_ws_notes_url(workspace_id))
    assert r.status_code == 200
    keys = [n["key"] for n in r.json()]
    assert "alpha" in keys
    assert "beta" in keys


def test_ws_note_patch_value(client, workspace_id):
    client.post(_ws_notes_url(workspace_id), json={"key": "x", "value": "old", "tags": []})
    r = client.patch(_ws_notes_url(workspace_id, "x"), json={"value": "new"})
    assert r.status_code == 200
    assert r.json()["value"] == "new"


def test_ws_note_patch_tags(client, workspace_id):
    client.post(_ws_notes_url(workspace_id), json={"key": "y", "value": "val", "tags": ["a"]})
    r = client.patch(_ws_notes_url(workspace_id, "y"), json={"tags": ["a", "b"]})
    assert r.status_code == 200
    assert set(r.json()["tags"]) == {"a", "b"}


def test_ws_note_delete(client, workspace_id):
    client.post(_ws_notes_url(workspace_id), json={"key": "z", "value": "v", "tags": []})
    r = client.delete(_ws_notes_url(workspace_id, "z"))
    assert r.status_code == 204
    assert client.get(_ws_notes_url(workspace_id, "z")).status_code == 404


# ---------------------------------------------------------------------------
# 2. CRUD round-trip — topic-scoped notes
# ---------------------------------------------------------------------------


def test_topic_note_create(client, workspace_id, topic_id):
    r = client.post(
        _topic_notes_url(workspace_id, topic_id),
        json={"key": "plan", "value": "refactor", "tags": ["context"]},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["key"] == "plan"
    assert body["scope_type"] == "topic"
    assert body["scope_id"] == topic_id


def test_topic_note_get(client, workspace_id, topic_id):
    client.post(
        _topic_notes_url(workspace_id, topic_id),
        json={"key": "t1", "value": "tv1", "tags": []},
    )
    r = client.get(_topic_notes_url(workspace_id, topic_id, "t1"))
    assert r.status_code == 200
    assert r.json()["value"] == "tv1"


def test_topic_note_list(client, workspace_id, topic_id):
    client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "n1", "value": "1", "tags": []})
    client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "n2", "value": "2", "tags": []})
    r = client.get(_topic_notes_url(workspace_id, topic_id))
    assert r.status_code == 200
    keys = [n["key"] for n in r.json()]
    assert "n1" in keys
    assert "n2" in keys


def test_topic_note_patch_value(client, workspace_id, topic_id):
    client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "p", "value": "old", "tags": []})
    r = client.patch(_topic_notes_url(workspace_id, topic_id, "p"), json={"value": "new"})
    assert r.status_code == 200
    assert r.json()["value"] == "new"


def test_topic_note_patch_tags(client, workspace_id, topic_id):
    client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "q", "value": "v", "tags": ["x"]})
    r = client.patch(_topic_notes_url(workspace_id, topic_id, "q"), json={"tags": ["x", "y"]})
    assert r.status_code == 200
    assert set(r.json()["tags"]) == {"x", "y"}


def test_topic_note_delete(client, workspace_id, topic_id):
    client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "d", "value": "v", "tags": []})
    r = client.delete(_topic_notes_url(workspace_id, topic_id, "d"))
    assert r.status_code == 204
    assert client.get(_topic_notes_url(workspace_id, topic_id, "d")).status_code == 404


# ---------------------------------------------------------------------------
# 3. Duplicate key on POST → 409
# ---------------------------------------------------------------------------


def test_ws_note_duplicate_409(client, workspace_id):
    client.post(_ws_notes_url(workspace_id), json={"key": "dup", "value": "first", "tags": []})
    r = client.post(_ws_notes_url(workspace_id), json={"key": "dup", "value": "second", "tags": []})
    assert r.status_code == 409


def test_topic_note_duplicate_409(client, workspace_id, topic_id):
    client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "dup", "value": "first", "tags": []})
    r = client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "dup", "value": "second", "tags": []})
    assert r.status_code == 409


def test_ws_duplicate_is_scoped_to_workspace(client, workspace_id):
    """Same key in two different workspaces must not conflict."""
    ws2 = client.post(
        "/api/workspaces",
        json={"name": "ws2", "repo_url": "https://github.com/x/z"},
    ).json()["id"]
    r1 = client.post(_ws_notes_url(workspace_id), json={"key": "shared", "value": "a", "tags": []})
    r2 = client.post(_ws_notes_url(ws2), json={"key": "shared", "value": "b", "tags": []})
    assert r1.status_code == 201
    assert r2.status_code == 201


# ---------------------------------------------------------------------------
# 4. GET/PATCH/DELETE non-existent key → 404
# ---------------------------------------------------------------------------


def test_ws_note_get_not_found(client, workspace_id):
    assert client.get(_ws_notes_url(workspace_id, "nosuchkey")).status_code == 404


def test_ws_note_patch_not_found(client, workspace_id):
    assert client.patch(_ws_notes_url(workspace_id, "nosuchkey"), json={"value": "v"}).status_code == 404


def test_ws_note_delete_not_found(client, workspace_id):
    assert client.delete(_ws_notes_url(workspace_id, "nosuchkey")).status_code == 404


def test_topic_note_get_not_found(client, workspace_id, topic_id):
    assert client.get(_topic_notes_url(workspace_id, topic_id, "nosuch")).status_code == 404


def test_topic_note_patch_not_found(client, workspace_id, topic_id):
    assert client.patch(
        _topic_notes_url(workspace_id, topic_id, "nosuch"), json={"value": "v"}
    ).status_code == 404


def test_topic_note_delete_not_found(client, workspace_id, topic_id):
    assert client.delete(_topic_notes_url(workspace_id, topic_id, "nosuch")).status_code == 404


# ---------------------------------------------------------------------------
# 5. PATCH with `key` field present → 422 (extra="forbid" on NotePatch)
# ---------------------------------------------------------------------------


def test_ws_note_patch_with_key_field_422(client, workspace_id):
    client.post(_ws_notes_url(workspace_id), json={"key": "k", "value": "v", "tags": []})
    r = client.patch(_ws_notes_url(workspace_id, "k"), json={"key": "new-key", "value": "v"})
    assert r.status_code == 422


def test_topic_note_patch_with_key_field_422(client, workspace_id, topic_id):
    client.post(_topic_notes_url(workspace_id, topic_id), json={"key": "k", "value": "v", "tags": []})
    r = client.patch(_topic_notes_url(workspace_id, topic_id, "k"), json={"key": "nk", "value": "v"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 6–8. render_template — tag filtering, sorted output, empty-match
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_with_notes(tmp_path):
    """Return (db_path, workspace_id) with two notes pre-seeded."""
    db_path = str(tmp_path / "notes_test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    ws_id = "ws-render-test"
    # Create a workspace row so foreign-key checks (if any) are satisfied
    conn.execute(
        "INSERT INTO workspaces (id, name, repo_url, created_at)"
        " VALUES (?, 'test', 'https://x.y', '2026-01-01T00:00:00Z')",
        (ws_id,),
    )
    # Note 1: tagged ["memory","context"]
    conn.execute(
        "INSERT INTO notes (id, scope_type, scope_id, key, value, tags, created_at, updated_at)"
        " VALUES ('n1','workspace',?,?,?,?,?,?)",
        (ws_id, "aardvark", "always start here", '["memory","context"]',
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    # Note 2: tagged ["memory"]
    conn.execute(
        "INSERT INTO notes (id, scope_type, scope_id, key, value, tags, created_at, updated_at)"
        " VALUES ('n2','workspace',?,?,?,?,?,?)",
        (ws_id, "zebra", "last item", '["memory"]',
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    return db_path, ws_id


def test_tag_filtering_memory_tag_matches_both(db_with_notes):
    """Notes tagged 'memory' appear in {ws:note:notelist:memory}."""
    db_path, ws_id = db_with_notes
    result = render_template(
        "{ws:note:notelist:memory}",
        {},
        db_path=db_path,
        workspace_id=ws_id,
    )
    assert "aardvark: always start here" in result
    assert "zebra: last item" in result


def test_tag_filtering_context_tag_matches_only_one(db_with_notes):
    """Only the note tagged 'context' appears in {ws:note:notelist:context}."""
    db_path, ws_id = db_with_notes
    result = render_template(
        "{ws:note:notelist:context}",
        {},
        db_path=db_path,
        workspace_id=ws_id,
    )
    assert "aardvark: always start here" in result
    assert "zebra" not in result


def test_tag_filtering_unrelated_tag_matches_none(db_with_notes):
    """{ws:note:notelist:goal} → empty string (no note has that tag)."""
    db_path, ws_id = db_with_notes
    result = render_template(
        "{ws:note:notelist:goal}",
        {},
        db_path=db_path,
        workspace_id=ws_id,
    )
    assert result == ""


# ---------------------------------------------------------------------------
# 7. notelist output is sorted by key
# ---------------------------------------------------------------------------


def test_notelist_sorted_by_key(db_with_notes):
    """Two notes tagged 'memory' → output sorted alphabetically by key."""
    db_path, ws_id = db_with_notes
    result = render_template(
        "{ws:note:notelist:memory}",
        {},
        db_path=db_path,
        workspace_id=ws_id,
    )
    lines = result.splitlines()
    assert len(lines) == 2
    assert lines[0] == "aardvark: always start here"
    assert lines[1] == "zebra: last item"


# ---------------------------------------------------------------------------
# 8. Empty tag match → empty string (not literal marker)
# ---------------------------------------------------------------------------


def test_empty_tag_match_returns_empty_string(db_with_notes):
    """A tag with no matching notes must produce an empty string, not the raw marker."""
    db_path, ws_id = db_with_notes
    result = render_template(
        "PREFIX{ws:note:notelist:nonexistent}SUFFIX",
        {},
        db_path=db_path,
        workspace_id=ws_id,
    )
    assert result == "PREFIXSUFFIX"
    assert "{ws:note:notelist:nonexistent}" not in result


# ---------------------------------------------------------------------------
# 9. {t:note:notelist:…} → empty string + WARNING
# ---------------------------------------------------------------------------


def test_topic_scope_marker_returns_empty_and_warns(db_with_notes, caplog):
    """v1 does not support topic-scoped note markers; must warn and return empty."""
    db_path, ws_id = db_with_notes
    with caplog.at_level(logging.WARNING, logger="src.master.event_dispatcher"):
        result = render_template(
            "{t:note:notelist:memory}",
            {},
            db_path=db_path,
            workspace_id=ws_id,
        )
    assert result == ""
    assert any("scope_unsupported_in_v1" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 10. {variable} placeholders coexist with note markers
# ---------------------------------------------------------------------------


def test_variable_and_note_marker_coexistence(db_with_notes):
    """Both {variable} and {ws:note:notelist:tag} resolve correctly in one pass."""
    db_path, ws_id = db_with_notes
    result = render_template(
        "Hello {name}! Notes: {ws:note:notelist:context}",
        {"name": "World"},
        db_path=db_path,
        workspace_id=ws_id,
    )
    assert "Hello World!" in result
    assert "aardvark: always start here" in result


def test_variable_resolved_alongside_note_marker(db_with_notes):
    """A single template string with both kinds of markers resolves both correctly."""
    db_path, ws_id = db_with_notes
    result = render_template(
        "{greeting} {ws:note:notelist:memory} {farewell}",
        {"greeting": "START", "farewell": "END"},
        db_path=db_path,
        workspace_id=ws_id,
    )
    assert result.startswith("START ")
    assert result.endswith(" END")
    assert "aardvark" in result
    assert "zebra" in result


# ---------------------------------------------------------------------------
# 11. render_template without db_path/workspace_id → note markers → empty + WARNING
# ---------------------------------------------------------------------------


def test_no_db_context_note_marker_returns_empty_and_warns(caplog):
    """When db_path or workspace_id is None, note markers must produce empty + warning."""
    with caplog.at_level(logging.WARNING, logger="src.master.event_dispatcher"):
        result = render_template(
            "Before{ws:note:notelist:memory}After",
            {"unused": "x"},
        )
    assert result == "BeforeAfter"
    assert any("note_marker.no_db_context" in r.message for r in caplog.records)


def test_no_db_context_variable_still_resolves(caplog):
    """When db context is absent, {variable} substitution still works."""
    with caplog.at_level(logging.WARNING, logger="src.master.event_dispatcher"):
        result = render_template(
            "{name} says {ws:note:notelist:memory}",
            {"name": "Alice"},
        )
    assert result.startswith("Alice says ")
    assert "{name}" not in result


# ---------------------------------------------------------------------------
# 12. Unknown {variable} → left literal + WARNING
# ---------------------------------------------------------------------------


def test_unknown_variable_left_literal_and_warns(caplog):
    """{unknown_var} must remain in the output unchanged and produce a warning."""
    with caplog.at_level(logging.WARNING, logger="src.master.event_dispatcher"):
        result = render_template(
            "Hello {unknown_var}!",
            {},
        )
    assert result == "Hello {unknown_var}!"
    assert any("render_template.unknown_variable" in r.message for r in caplog.records)


def test_known_variable_resolves_unknown_stays(caplog):
    """Known variables resolve; unknown ones stay literal."""
    with caplog.at_level(logging.WARNING, logger="src.master.event_dispatcher"):
        result = render_template(
            "{greeting} from {unknown_person}",
            {"greeting": "Hi"},
        )
    assert result == "Hi from {unknown_person}"


# ---------------------------------------------------------------------------
# 13. Staff system_prompt with note marker → injected into MQTT payload
# ---------------------------------------------------------------------------


def test_staff_system_prompt_note_injection(tmp_path, monkeypatch):
    """A staff whose system_prompt contains {ws:note:notelist:memory} must have
    the note values injected into the MQTT dispatch payload."""
    monkeypatch.setenv("MASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_RUNTIME", "docker")
    monkeypatch.setenv("MASTER_DRY_RUN", "true")
    with patch("src.master.main.build_mqtt_client") as mock_build:
        mock_mqtt = MagicMock()
        mock_build.return_value = mock_mqtt
        with TestClient(app) as c:
            # Create workspace and topic
            ws_id = c.post(
                "/api/workspaces",
                json={"name": "inject-ws", "repo_url": "https://github.com/x/y"},
            ).json()["id"]
            topic_id = c.post(
                f"/api/workspaces/{ws_id}/topics",
                json={"subject": "inject-topic", "repo_ref": "main"},
            ).json()["id"]

            # Create a workspace note tagged "memory"
            c.post(
                f"/api/workspaces/{ws_id}/notes",
                json={"key": "objective", "value": "pass all tests", "tags": ["memory"]},
            )

            # Update the default 'claude' staff to use a note-marker system_prompt
            c.put(
                f"/api/workspaces/{ws_id}/staffs/claude",
                json={
                    "name": "claude",
                    "adapter": "claude-code",
                    "system_prompt": "Context: {ws:note:notelist:memory}",
                    "is_default": True,
                },
            )

            # Send a message to trigger dispatch
            r = c.post(
                f"/api/workspaces/{ws_id}/topics/{topic_id}/messages",
                data={"text": "@claude hello"},
            )
            assert r.status_code == 202

            # Inspect the MQTT payload
            assert mock_mqtt.publish.called
            payload = json.loads(mock_mqtt.publish.call_args.args[1])
            system_prompt = payload.get("system_prompt", "")
            assert "objective: pass all tests" in system_prompt, (
                f"Note not injected into system_prompt. Got: {system_prompt!r}"
            )
