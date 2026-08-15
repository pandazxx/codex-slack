"""Orchestration MCP server — exposes delegate_task and ask_sender as MCP tools.

Reads from environment:
  MASTER_URL        — e.g. http://master:8080
  WORKSPACE_ID      — set at container spawn time by agent_runner.py
  TOPIC_ID          — set per-prompt by mqtt_loop.py
  AGENT_NAME        — the name of the calling staff
  PROMPT_MESSAGE_ID — the message_id of the prompt this turn is answering
  TASK_DEPTH        — depth of the current task (0 for direct user→staff turns)

Tool gating: delegate_task is only registered when TASK_DEPTH < MAX_DELEGATION_DEPTH
so agents at the depth ceiling cannot see or call it.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

from src.master.orchestration import MAX_DELEGATION_DEPTH

mcp = FastMCP("orchestrate")

# Import-time env capture is safe here because each agent turn spawns a fresh
# CLI subprocess, which in turn spawns a fresh stdio MCP process.  The process
# starts, handles one tool call, and exits — so these module-level reads are
# effectively per-turn.  If the CLI is ever changed to reuse MCP processes
# across turns (e.g. a long-lived MCP server mode), these must move to
# call-time reads inside each tool function.
_BASE = os.environ.get("MASTER_URL", "http://master:8080")
_WS = os.environ.get("WORKSPACE_ID", "")
_TOPIC_ID = os.environ.get("TOPIC_ID", "")
_AGENT_NAME = os.environ.get("AGENT_NAME", "")
_PROMPT_MESSAGE_ID = os.environ.get("PROMPT_MESSAGE_ID", "")
_TASK_DEPTH = int(os.environ.get("TASK_DEPTH", "0") or "0")

_ORCH_BASE = f"{_BASE}/api/workspaces/{_WS}/topics/{_TOPIC_ID}/orchestrate"


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _parse_json(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except Exception:
        ct = resp.headers.get("content-type", "?")
        raise RuntimeError(
            f"orchestrate API returned non-JSON (status={resp.status_code}, "
            f"content-type={ct!r}, url={resp.url}) — "
            "master service may be outdated or env vars may be unset"
        )


if _TASK_DEPTH < MAX_DELEGATION_DEPTH:
    @mcp.tool()
    def delegate_task(
        staff: str,
        goal: str,
        acceptance_criteria: str,
        context: str | None = None,
    ) -> dict:
        """Hand a subtask to another staff member.

        Creates a task row and dispatches the assignee's first prompt.
        Returns task_id and the resulting task state.

        Only available when the current turn depth is below max_delegation_depth.
        Fails if the target staff cannot be resolved, if you delegate to yourself,
        if the fan-out limit is reached, or if delegation depth would be exceeded.
        """
        with _client() as c:
            resp = c.post(
                f"{_ORCH_BASE}/delegate",
                json={
                    "caller_staff": _AGENT_NAME,
                    "caller_message_id": _PROMPT_MESSAGE_ID,
                    "staff": staff,
                    "goal": goal,
                    "acceptance_criteria": acceptance_criteria,
                    "context": context,
                },
            )
            if resp.status_code == 422:
                detail = resp.json().get("detail", "validation_error")
                raise RuntimeError(f"delegate_task rejected: {detail}")
            if resp.status_code == 404:
                detail = resp.json().get("detail", "not_found")
                raise RuntimeError(f"delegate_task: {detail}")
            resp.raise_for_status()
            return _parse_json(resp)


@mcp.tool()
def ask_sender(question: str) -> dict:
    """Ask a clarifying question of whoever dispatched this turn.

    If inside a delegated task, asks the task's dispatcher (another staff)
    and transitions the task to input-required.
    If at depth 0 (direct user→staff turn), asks the user.

    In phase (a) the question is recorded and broadcast; re-dispatch to a
    staff dispatcher is wired in phase (b).
    """
    with _client() as c:
        resp = c.post(
            f"{_ORCH_BASE}/ask",
            json={
                "caller_staff": _AGENT_NAME,
                "caller_message_id": _PROMPT_MESSAGE_ID,
                "question": question,
            },
        )
        if resp.status_code in (404, 422):
            detail = resp.json().get("detail", "error")
            raise RuntimeError(f"ask_sender rejected: {detail}")
        resp.raise_for_status()
        return _parse_json(resp)
