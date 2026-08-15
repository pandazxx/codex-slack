"""Orchestration MCP server — exposes orchestration tools as MCP tools.

Reads from environment:
  MASTER_URL          — e.g. http://master:8080
  WORKSPACE_ID        — set at container spawn time by agent_runner.py
  TOPIC_ID            — set per-prompt by mqtt_loop.py
  AGENT_NAME          — the name of the calling staff
  PROMPT_MESSAGE_ID   — the message_id of the prompt this turn is answering
  DISPATCH_TOKEN      — per-dispatch auth token (stored in messages.dispatch_token)
  TASK_DEPTH          — depth of the current task (0 for direct user→staff turns)
  MAX_DELEGATION_DEPTH — effective depth limit from runtime config (defaults to module constant)

Tool gating:
  - delegate_task: only when TASK_DEPTH < MAX_DELEGATION_DEPTH
  - submit_result: only when TASK_DEPTH >= 1 (assignee in a delegated task)
  - answer_question / accept_result: only when TASK_DEPTH == 0 (dispatcher turn)
  - ask_sender: always
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
_DISPATCH_TOKEN = os.environ.get("DISPATCH_TOKEN", "")
_TASK_DEPTH = int(os.environ.get("TASK_DEPTH", "0") or "0")
_MAX_DELEGATION_DEPTH = int(
    os.environ.get("MAX_DELEGATION_DEPTH", str(MAX_DELEGATION_DEPTH)) or str(MAX_DELEGATION_DEPTH)
)

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


def _common_payload() -> dict:
    """Base fields present in every orchestrate API request."""
    return {
        "caller_staff": _AGENT_NAME,
        "caller_message_id": _PROMPT_MESSAGE_ID,
        "dispatch_token": _DISPATCH_TOKEN or None,
    }


if _TASK_DEPTH < _MAX_DELEGATION_DEPTH:
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
                    **_common_payload(),
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
            if resp.status_code == 403:
                detail = resp.json().get("detail", "forbidden")
                raise RuntimeError(f"delegate_task forbidden: {detail}")
            resp.raise_for_status()
            return _parse_json(resp)


@mcp.tool()
def ask_sender(question: str) -> dict:
    """Ask a clarifying question of whoever dispatched this turn.

    If inside a delegated task, asks the task's dispatcher (another staff)
    and transitions the task to input-required.
    If at depth 0 (direct user→staff turn), asks the user.
    """
    with _client() as c:
        resp = c.post(
            f"{_ORCH_BASE}/ask",
            json={**_common_payload(), "question": question},
        )
        if resp.status_code in (404, 422):
            detail = resp.json().get("detail", "error")
            raise RuntimeError(f"ask_sender rejected: {detail}")
        if resp.status_code == 403:
            detail = resp.json().get("detail", "forbidden")
            raise RuntimeError(f"ask_sender forbidden: {detail}")
        resp.raise_for_status()
        return _parse_json(resp)


if _TASK_DEPTH >= 1:
    @mcp.tool()
    def submit_result(
        status: str,
        summary: str,
        artifacts: list[dict] | None = None,
    ) -> dict:
        """Submit the result of the current delegated task.

        status: 'completed' or 'failed' — your own assessment.
        summary: one-paragraph result summary.
        artifacts: optional list of {kind: 'commit'|'file', ref: str} dicts.

        Available only on depth-≥1 turns (inside a delegated task).
        Calling it twice in one turn overwrites (last call wins).
        """
        with _client() as c:
            resp = c.post(
                f"{_ORCH_BASE}/submit_result",
                json={
                    **_common_payload(),
                    "status": status,
                    "summary": summary,
                    "artifacts": artifacts or [],
                },
            )
            if resp.status_code in (404, 422):
                detail = resp.json().get("detail", "error")
                raise RuntimeError(f"submit_result rejected: {detail}")
            if resp.status_code == 403:
                detail = resp.json().get("detail", "forbidden")
                raise RuntimeError(f"submit_result forbidden: {detail}")
            if resp.status_code == 409:
                detail = resp.json().get("detail", "conflict")
                raise RuntimeError(f"submit_result conflict: {detail}")
            resp.raise_for_status()
            return _parse_json(resp)


if _TASK_DEPTH == 0:
    @mcp.tool()
    def answer_question(task_id: str, answer: str) -> dict:
        """Answer a pending ask_sender question on a task you dispatched.

        Records the answer as a structured, addressed message (receiver = the
        asking assignee) and returns the task to 'working'. Master dispatches
        the answer to the assignee when your turn ends.

        Only available on depth-0 turns (you are a dispatcher, not an assignee).
        """
        with _client() as c:
            resp = c.post(
                f"{_ORCH_BASE}/answer_question",
                json={
                    **_common_payload(),
                    "task_id": task_id,
                    "answer": answer,
                },
            )
            if resp.status_code in (404, 422):
                detail = resp.json().get("detail", "error")
                raise RuntimeError(f"answer_question rejected: {detail}")
            if resp.status_code == 403:
                detail = resp.json().get("detail", "forbidden")
                raise RuntimeError(f"answer_question forbidden: {detail}")
            if resp.status_code == 409:
                detail = resp.json().get("detail", "conflict")
                raise RuntimeError(f"answer_question conflict: {detail}")
            resp.raise_for_status()
            return _parse_json(resp)

    @mcp.tool()
    def accept_result(task_id: str) -> dict:
        """Accept the result of a task you dispatched and close it as completed.

        Callable only on the judgment turn immediately after the assignee submitted
        their result.  The result-of-record is whatever the assignee last passed to
        submit_result (or the implicit-result fallback).

        Only available on depth-0 turns (you are a dispatcher, not an assignee).
        """
        with _client() as c:
            resp = c.post(
                f"{_ORCH_BASE}/accept_result",
                json={
                    **_common_payload(),
                    "task_id": task_id,
                },
            )
            if resp.status_code in (404, 422):
                detail = resp.json().get("detail", "error")
                raise RuntimeError(f"accept_result rejected: {detail}")
            if resp.status_code == 403:
                detail = resp.json().get("detail", "forbidden")
                raise RuntimeError(f"accept_result forbidden: {detail}")
            if resp.status_code == 409:
                detail = resp.json().get("detail", "conflict")
                raise RuntimeError(f"accept_result conflict: {detail}")
            resp.raise_for_status()
            return _parse_json(resp)
