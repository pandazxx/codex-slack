"""Notes MCP server — exposes workspace and topic note CRUD as MCP tools.

Reads from environment:
  MASTER_URL    — e.g. http://master:8080
  WORKSPACE_ID  — set at container spawn time by agent_runner.py

Topic tools accept topic_id as an explicit argument so they work regardless
of when the MCP server process was started.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("notes")

_BASE = os.environ.get("MASTER_URL", "http://master:8080")
_WS   = os.environ.get("WORKSPACE_ID", "")

_WS_BASE = f"{_BASE}/api/workspaces/{_WS}/notes"


def _t_base(topic_id: str) -> str:
    return f"{_BASE}/api/workspaces/{_WS}/topics/{topic_id}/notes"


def _client() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def _parse_json(resp: httpx.Response) -> object:
    """Parse JSON response, raising a descriptive error for non-JSON bodies."""
    try:
        return resp.json()
    except Exception:
        ct = resp.headers.get("content-type", "?")
        raise RuntimeError(
            f"notes API returned non-JSON (status={resp.status_code}, "
            f"content-type={ct!r}, url={resp.url}) — "
            "master service may be outdated or WORKSPACE_ID may be empty"
        )


# ── Workspace-scoped tools ────────────────────────────────────────────────────

@mcp.tool()
def list_workspace_notes(tag: str | None = None) -> list[dict]:
    """List workspace notes. Pass a tag to filter (e.g. 'memory', 'context')."""
    with _client() as c:
        notes = _parse_json(c.get(_WS_BASE).raise_for_status())
    if tag:
        notes = [n for n in notes if tag in n.get("tags", [])]
    return notes


@mcp.tool()
def get_workspace_note(key: str) -> dict:
    """Get a single workspace note by key."""
    with _client() as c:
        return _parse_json(c.get(f"{_WS_BASE}/{key}").raise_for_status())


@mcp.tool()
def create_workspace_note(key: str, value: str, tags: list[str] | None = None) -> dict:
    """Create a workspace note. key must be a unique slug. Returns 409 if key already exists."""
    with _client() as c:
        return _parse_json(c.post(_WS_BASE, json={"key": key, "value": value, "tags": tags or []}).raise_for_status())


@mcp.tool()
def update_workspace_note(key: str, value: str | None = None, tags: list[str] | None = None) -> dict:
    """Update value and/or tags of an existing workspace note. key is immutable."""
    body: dict = {}
    if value is not None:
        body["value"] = value
    if tags is not None:
        body["tags"] = tags
    with _client() as c:
        return _parse_json(c.patch(f"{_WS_BASE}/{key}", json=body).raise_for_status())


@mcp.tool()
def delete_workspace_note(key: str) -> str:
    """Delete a workspace note by key. Returns confirmation string."""
    with _client() as c:
        c.delete(f"{_WS_BASE}/{key}").raise_for_status()
    return f"Deleted workspace note: {key}"


# ── Topic-scoped tools ───────────────────────────────────────────────────────

@mcp.tool()
def list_topic_notes(topic_id: str, tag: str | None = None) -> list[dict]:
    """List notes for a topic. Pass the current TOPIC_ID env var as topic_id. Optionally filter by tag."""
    with _client() as c:
        notes = _parse_json(c.get(_t_base(topic_id)).raise_for_status())
    if tag:
        notes = [n for n in notes if tag in n.get("tags", [])]
    return notes


@mcp.tool()
def create_topic_note(topic_id: str, key: str, value: str, tags: list[str] | None = None) -> dict:
    """Create a note scoped to a topic. Pass the current TOPIC_ID env var as topic_id."""
    with _client() as c:
        return _parse_json(c.post(_t_base(topic_id), json={"key": key, "value": value, "tags": tags or []}).raise_for_status())


@mcp.tool()
def update_topic_note(topic_id: str, key: str, value: str | None = None, tags: list[str] | None = None) -> dict:
    """Update value and/or tags of an existing topic note. Pass the current TOPIC_ID env var as topic_id."""
    body: dict = {}
    if value is not None:
        body["value"] = value
    if tags is not None:
        body["tags"] = tags
    with _client() as c:
        return _parse_json(c.patch(f"{_t_base(topic_id)}/{key}", json=body).raise_for_status())


@mcp.tool()
def delete_topic_note(topic_id: str, key: str) -> str:
    """Delete a topic note by key. Pass the current TOPIC_ID env var as topic_id."""
    with _client() as c:
        c.delete(f"{_t_base(topic_id)}/{key}").raise_for_status()
    return f"Deleted topic note: {key}"
