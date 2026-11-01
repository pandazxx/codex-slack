"""Smoke tests for API endpoints not covered by other test files.

Each test exercises at least one HTTP call against an uncovered route and
asserts the response status code is in the expected range.  These tests exist
to satisfy the route-coverage guard (tests/conftest.py::_install_route_tracker)
and to catch Postgres-incompatible SQL regressions early.

Routes covered here:
  POST /api/workspaces/{workspace_id}/refresh-auth
  POST /api/workspaces/{workspace_id}/restart-agent
  PATCH /api/workspaces/{workspace_id}/topics/{topic_id}/current-staff
  POST /api/workspaces/{workspace_id}/topics/{topic_id}/messages/{message_id}/cancel
  POST /api/staffs/{name}/set-default
  POST /api/workspaces/{workspace_id}/staffs/{name}/set-default
  PUT  /api/workspaces/{workspace_id}/topics/{topic_id}/staffs/{name}
  DELETE /api/workspaces/{workspace_id}/topics/{topic_id}/staffs/{name}
  GET  /api/config/system-variables
  PATCH /api/config/system-settings
  GET  /api/workspaces/{workspace_id}/event-actions
  POST /api/workspaces/{workspace_id}/event-actions
  GET  /api/workspaces/{workspace_id}/event-actions/{action_id}
  PATCH /api/workspaces/{workspace_id}/event-actions/{action_id}
  DELETE /api/workspaces/{workspace_id}/event-actions/{action_id}
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.master.main import app


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
def workspace(client):
    r = client.post("/api/workspaces", json={"name": "Test WS", "repo_url": "https://github.com/x/y"})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def topic(client, workspace):
    r = client.post(
        f"/api/workspaces/{workspace['id']}/topics",
        json={"subject": "Fix the bug", "repo_ref": "main"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def global_staff(client):
    r = client.post(
        "/api/staffs",
        json={"name": "reviewer", "adapter": "claude-code", "session_scope": "topic"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def workspace_staff(client, workspace):
    r = client.post(
        f"/api/workspaces/{workspace['id']}/staffs",
        json={"name": "ws-reviewer", "adapter": "claude-code", "session_scope": "topic"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def topic_staff(client, workspace, topic):
    r = client.post(
        f"/api/workspaces/{workspace['id']}/topics/{topic['id']}/staffs",
        json={"name": "topic-reviewer", "adapter": "claude-code", "session_scope": "topic"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def message(client, workspace, topic):
    r = client.post(
        f"/api/workspaces/{workspace['id']}/topics/{topic['id']}/messages",
        data={"text": "hello"},
    )
    assert r.status_code == 202, r.text
    return r.json()


# ---------------------------------------------------------------------------
# POST /api/workspaces/{workspace_id}/refresh-auth
# ---------------------------------------------------------------------------

class TestRefreshAuth:
    def test_refresh_auth_known_workspace_returns_200(self, client, workspace):
        """Known workspace returns 200 with refreshed_at; docker call is mocked."""
        with patch("src.master.workspaces.refresh_auth"):
            r = client.post(f"/api/workspaces/{workspace['id']}/refresh-auth")
        assert r.status_code == 200
        assert "refreshed_at" in r.json()

    def test_refresh_auth_unknown_workspace_returns_404(self, client):
        r = client.post("/api/workspaces/nonexistent-id/refresh-auth")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/workspaces/{workspace_id}/restart-agent
# ---------------------------------------------------------------------------

class TestRestartAgent:
    def test_restart_agent_known_workspace_returns_200(self, client, workspace):
        """Known workspace triggers agent restart; container ops are mocked."""
        with (
            patch("src.master.workspaces.stop_agent"),
            patch("src.master.workspaces.spawn_agent", return_value="cname"),
        ):
            r = client.post(f"/api/workspaces/{workspace['id']}/restart-agent")
        assert r.status_code == 200

    def test_restart_agent_unknown_workspace_returns_404(self, client):
        r = client.post("/api/workspaces/nonexistent-id/restart-agent")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/workspaces/{workspace_id}/topics/{topic_id}/current-staff
# ---------------------------------------------------------------------------

class TestCurrentStaff:
    def test_set_current_staff_updates_topic(self, client, workspace, topic):
        r = client.patch(
            f"/api/workspaces/{workspace['id']}/topics/{topic['id']}/current-staff",
            json={"name": "architect"},
        )
        assert r.status_code == 200
        assert r.json()["current_staff_name"] == "architect"

    def test_clear_current_staff_sets_null(self, client, workspace, topic):
        r = client.patch(
            f"/api/workspaces/{workspace['id']}/topics/{topic['id']}/current-staff",
            json={"name": None},
        )
        assert r.status_code == 200
        assert r.json()["current_staff_name"] is None

    def test_current_staff_unknown_topic_returns_404(self, client, workspace):
        r = client.patch(
            f"/api/workspaces/{workspace['id']}/topics/nonexistent/current-staff",
            json={"name": "architect"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/workspaces/{workspace_id}/topics/{topic_id}/messages/{message_id}/cancel
# ---------------------------------------------------------------------------

class TestCancelMessage:
    def test_cancel_message_returns_202(self, client, workspace, topic, message):
        """cancel endpoint publishes to MQTT and returns 202 with status."""
        r = client.post(
            f"/api/workspaces/{workspace['id']}/topics/{topic['id']}"
            f"/messages/{message['message_id']}/cancel"
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "cancel_requested"
        assert body["message_id"] == message["message_id"]


# ---------------------------------------------------------------------------
# POST /api/staffs/{name}/set-default
# ---------------------------------------------------------------------------

class TestSetDefaultGlobalStaff:
    def test_set_default_global_staff_returns_200(self, client, global_staff):
        r = client.post(f"/api/staffs/{global_staff['name']}/set-default")
        assert r.status_code == 200
        assert r.json()["is_default"] is True

    def test_set_default_unknown_staff_returns_404(self, client):
        r = client.post("/api/staffs/nonexistent/set-default")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/workspaces/{workspace_id}/staffs/{name}/set-default
# ---------------------------------------------------------------------------

class TestSetDefaultWorkspaceStaff:
    def test_set_default_workspace_staff_returns_200(self, client, workspace, workspace_staff):
        r = client.post(
            f"/api/workspaces/{workspace['id']}/staffs/{workspace_staff['name']}/set-default"
        )
        assert r.status_code == 200
        assert r.json()["is_default"] is True

    def test_set_default_unknown_workspace_staff_returns_404(self, client, workspace):
        r = client.post(
            f"/api/workspaces/{workspace['id']}/staffs/nonexistent/set-default"
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT/DELETE /api/workspaces/{workspace_id}/topics/{topic_id}/staffs/{name}
# ---------------------------------------------------------------------------

class TestTopicStaffCRUD:
    def test_put_topic_staff_updates_and_returns_200(self, client, workspace, topic, topic_staff):
        r = client.put(
            f"/api/workspaces/{workspace['id']}/topics/{topic['id']}"
            f"/staffs/{topic_staff['name']}",
            json={"name": topic_staff["name"], "adapter": "claude-code",
                  "session_scope": "workspace", "system_prompt": "Updated"},
        )
        assert r.status_code == 200
        assert r.json()["session_scope"] == "workspace"

    def test_delete_topic_staff_returns_204(self, client, workspace, topic, topic_staff):
        r = client.delete(
            f"/api/workspaces/{workspace['id']}/topics/{topic['id']}"
            f"/staffs/{topic_staff['name']}"
        )
        assert r.status_code == 204

    def test_delete_nonexistent_topic_staff_returns_404(self, client, workspace, topic):
        r = client.delete(
            f"/api/workspaces/{workspace['id']}/topics/{topic['id']}/staffs/nonexistent"
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/config/system-variables
# ---------------------------------------------------------------------------

class TestSystemVariables:
    def test_get_system_variables_returns_list(self, client):
        r = client.get("/api/config/system-variables")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0, "Expected at least one system variable to be defined"
        assert any(k in data[0] for k in ("name", "key", "variable"))


# ---------------------------------------------------------------------------
# PATCH /api/config/system-settings
# ---------------------------------------------------------------------------

class TestSystemSettings:
    def test_patch_system_settings_valid_tz_returns_200(self, client):
        r = client.patch("/api/config/system-settings", json={"timezone": "UTC"})
        assert r.status_code == 200
        assert r.json()["timezone"] == "UTC"

    def test_patch_system_settings_invalid_tz_returns_422(self, client):
        r = client.patch("/api/config/system-settings", json={"timezone": "Invalid/Timezone/Xyz"})
        assert r.status_code == 422

    def test_get_system_settings_after_patch(self, client):
        client.patch("/api/config/system-settings", json={"timezone": "America/New_York"})
        r = client.get("/api/config/system-settings")
        assert r.status_code == 200
        assert r.json()["timezone"] == "America/New_York"
        assert r.json()["timezone_configured"] is True


# ---------------------------------------------------------------------------
# Workspace-scoped event-actions
# GET/POST /api/workspaces/{workspace_id}/event-actions
# GET/PATCH/DELETE /api/workspaces/{workspace_id}/event-actions/{action_id}
# ---------------------------------------------------------------------------

_WS_ACTION_BODY = {
    "event_type": "topic_message_received",
    "staff_name": "reviewer",
    "prompt_template": "Summarise recent messages in {workspace_name}",
    "timing": None,
    "enabled": True,
}


class TestWorkspaceEventActions:
    def test_list_workspace_event_actions_empty(self, client, workspace):
        r = client.get(f"/api/workspaces/{workspace['id']}/event-actions")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_workspace_event_action_returns_201(self, client, workspace):
        r = client.post(
            f"/api/workspaces/{workspace['id']}/event-actions",
            json=_WS_ACTION_BODY,
        )
        assert r.status_code == 201
        body = r.json()
        assert body["event_type"] == "topic_message_received"
        assert body["scope_type"] == "workspace"
        assert body["scope_id"] == workspace["id"]
        assert body["staff_name"] == "reviewer"

    def test_get_workspace_event_action_returns_200(self, client, workspace):
        create = client.post(
            f"/api/workspaces/{workspace['id']}/event-actions",
            json=_WS_ACTION_BODY,
        )
        assert create.status_code == 201
        action_id = create.json()["id"]

        r = client.get(
            f"/api/workspaces/{workspace['id']}/event-actions/{action_id}"
        )
        assert r.status_code == 200
        assert r.json()["id"] == action_id

    def test_get_unknown_workspace_event_action_returns_404(self, client, workspace):
        r = client.get(
            f"/api/workspaces/{workspace['id']}/event-actions/no-such-id"
        )
        assert r.status_code == 404

    def test_patch_workspace_event_action_toggles_enabled(self, client, workspace):
        create = client.post(
            f"/api/workspaces/{workspace['id']}/event-actions",
            json=_WS_ACTION_BODY,
        )
        assert create.status_code == 201
        action_id = create.json()["id"]

        r = client.patch(
            f"/api/workspaces/{workspace['id']}/event-actions/{action_id}",
            json={"enabled": False},
        )
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_delete_workspace_event_action_returns_204(self, client, workspace):
        create = client.post(
            f"/api/workspaces/{workspace['id']}/event-actions",
            json=_WS_ACTION_BODY,
        )
        assert create.status_code == 201
        action_id = create.json()["id"]

        r = client.delete(
            f"/api/workspaces/{workspace['id']}/event-actions/{action_id}"
        )
        assert r.status_code == 204

        assert client.get(
            f"/api/workspaces/{workspace['id']}/event-actions/{action_id}"
        ).status_code == 404
