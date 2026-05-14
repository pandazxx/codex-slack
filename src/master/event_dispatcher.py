"""Event dispatch infrastructure: queue, worker, watchdog, emit_event, and veto_dispatch.

A single asyncio.Queue is owned by app.state. Emit sites call emit_event() from
any thread (FastAPI loop, MQTT thread, scheduler thread) and return immediately.
The single async event_worker task drains the queue — one event at a time, FIFO
across events — calling _handle_event for each. Within a single event, all
matching event_actions fire concurrently via asyncio.gather so a slow sibling
does not delay its peers or the next queued event.

veto_dispatch() is a separate synchronous-await path for pre-commit interceptors
(e.g. topic_archiving). It bypasses the queue, dispatches staff, and awaits
structured verdicts via asyncio.Future objects stored in app_state.veto_futures.

See ADR-0013 (post-commit events) and ADR-0014 (pre-commit veto) for rationale.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import NamedTuple

from .db import get_connection
from .staffs import resolve_staff

LOGGER = logging.getLogger(__name__)

# Wraps only the dispatch_to_staff call in _dispatch_one — i.e. the time to insert the
# message row, broadcast on the WS hub, and publish to MQTT. NOT a budget for the agent's
# LLM response, which is fully async (the agent reply arrives via MQTT minutes later).
DISPATCH_TIMEOUT_S = 10.0

# Budget for the agent to respond with a verdict. Covers LLM think time + MQTT round-trip.
VETO_TIMEOUT_S = 30.0


class VetoResult(NamedTuple):
    allowed: bool
    reason: str
    timed_out: bool


# ── Template rendering ────────────────────────────────────────────────────────

_TEMPLATE_RE = re.compile(
    r"\{(ws|t):note:keylist:([a-z0-9_-]+)\}"  # note marker
    r"|\{([a-zA-Z_][a-zA-Z0-9_]*)\}",          # plain variable
    re.IGNORECASE,
)


def render_template(
    template: str,
    variables: dict[str, str],
    *,
    db_path: str | None = None,
    workspace_id: str | None = None,
    topic_id: str | None = None,
) -> str:
    """Substitute {variable} placeholders and {ws:note:keylist:<tag>} markers in one pass."""
    def _resolve(m: re.Match) -> str:
        if m.group(1) is not None:  # note marker
            scope, tag = m.group(1).lower(), m.group(2)
            if scope == "t":
                LOGGER.warning("note_marker.scope_unsupported_in_v1 marker=%s", m.group(0))
                return ""
            if db_path is None or workspace_id is None:
                LOGGER.warning("note_marker.no_db_context marker=%s", m.group(0))
                return ""
            conn = get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT key, value FROM notes"
                    " WHERE scope_type='workspace' AND scope_id=?"
                    "   AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value=?)"
                    " ORDER BY key",
                    (workspace_id, tag),
                ).fetchall()
            finally:
                conn.close()
            return "\n".join(f"{r['key']}: {r['value']}" for r in rows)
        else:  # plain variable
            key = m.group(3)
            if key not in variables:
                LOGGER.warning("render_template.unknown_variable key=%s", key)
                return m.group(0)
            return variables[key]

    return _TEMPLATE_RE.sub(_resolve, template)


# ── emit_event — single threadsafe entry point ────────────────────────────────

def emit_event(
    *,
    app_state,
    event_type: str,
    topic_id: str,
    workspace_id: str,
    timing: str | None = None,
    variables: dict[str, str],
    scheduler_slot: datetime | None = None,
    scheduler_action_id: str | None = None,
) -> None:
    """Push an event onto the global event queue.

    Safe to call from any thread (FastAPI handler, MQTT thread, scheduler thread).
    Returns immediately; handling happens later in event_worker.
    """
    queue: asyncio.Queue | None = getattr(app_state, "event_queue", None)
    loop: asyncio.AbstractEventLoop | None = getattr(app_state, "event_loop", None)
    if queue is None or loop is None:
        # Event infrastructure not yet (or no longer) up — accept the loss and log.
        # Lifespan startup creates both before the FastAPI app accepts requests, so
        # this branch is reachable only during shutdown or in stripped-down test setups.
        LOGGER.debug("emit_event.dropped event_type=%s reason=infrastructure_absent", event_type)
        return
    event = {
        "event_type": event_type,
        "topic_id": topic_id,
        "workspace_id": workspace_id,
        "timing": timing,
        "variables": variables,
        "scheduler_slot": scheduler_slot,
        "scheduler_action_id": scheduler_action_id,
    }
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        queue.put_nowait(event)
    else:
        loop.call_soon_threadsafe(queue.put_nowait, event)


# ── event_worker — single async consumer ─────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def event_worker(app_state) -> None:
    """Drain the event queue one event at a time, FIFO.

    Per-event errors are caught and logged; the worker continues regardless.
    Updates app_state.event_worker_last_progress after each successfully
    processed event so the watchdog can detect stalls.
    """
    queue: asyncio.Queue = app_state.event_queue
    while True:
        event = await queue.get()
        try:
            await _handle_event(app_state, event)
            app_state.event_worker_last_progress = _now_utc()
        except Exception:
            LOGGER.exception(
                "event_worker.handle_failed type=%s",
                event.get("event_type"),
            )
        finally:
            queue.task_done()


async def _handle_event(app_state, event: dict) -> None:
    """Load matching event_actions and dispatch all of them in parallel.

    Loads both topic-scoped actions (for the specific topic) and workspace-scoped
    actions (configured at the workspace level, fired in the topic context).
    """
    conn = get_connection(app_state.db_path)
    try:
        topic_rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='topic'"
            "   AND scope_id=?"
            "   AND event_type=?"
            "   AND enabled=1"
            "   AND (timing IS NULL OR timing=?)",
            (event["topic_id"], event["event_type"], event["timing"]),
        ).fetchall()
        workspace_rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='workspace'"
            "   AND scope_id=?"
            "   AND event_type=?"
            "   AND enabled=1"
            "   AND (timing IS NULL OR timing=?)",
            (event["workspace_id"], event["event_type"], event["timing"]),
        ).fetchall()
        rows = list(topic_rows) + list(workspace_rows)
    finally:
        conn.close()

    # Gate actions (before + structured_output=1 for topic_message_sent) are handled
    # synchronously by run_gate_actions() in the message handler before dispatch.
    # Exclude them here to prevent double-dispatch.
    if event.get("event_type") == "topic_message_sent" and event.get("timing") == "before":
        rows = [r for r in rows if not r["structured_output"]]

    if not rows:
        return

    await asyncio.gather(
        *(_dispatch_one(app_state, row, event) for row in rows),
        return_exceptions=True,
    )


async def _dispatch_one(app_state, row, event: dict) -> None:
    """Dispatch a single matching action, recording last_run_* on completion."""
    from .dispatch import dispatch_to_staff  # local import avoids circular deps at module load

    db_path: str = app_state.db_path

    try:
        conn = get_connection(db_path)
        try:
            staff = resolve_staff(
                conn,
                row["staff_name"],
                event["workspace_id"],
                event["topic_id"],
            )
            topic_row = conn.execute(
                "SELECT t.id, t.subject, t.workspace_id, w.name AS workspace_name"
                " FROM topics t JOIN workspaces w ON w.id = t.workspace_id"
                " WHERE t.id = ?",
                (event["topic_id"],),
            ).fetchone()
        finally:
            conn.close()

        if staff is None:
            LOGGER.warning("event_action.staff_missing id=%s staff=%s", row["id"], row["staff_name"])
            _record_run(
                db_path,
                row["id"],
                status="staff_missing",
                output=f"staff_name={row['staff_name']!r} not resolvable at fire time",
            )
            return

        # Build variables: merge caller-supplied vars with standard structural vars.
        variables = dict(event["variables"])
        if topic_row:
            variables.setdefault("topic_json", json.dumps({
                "id": topic_row["id"],
                "subject": topic_row["subject"],
                "workspace_id": topic_row["workspace_id"],
                "workspace_name": topic_row["workspace_name"],
            }))

        try:
            prompt = render_template(
                row["prompt_template"], variables,
                db_path=db_path, workspace_id=event["workspace_id"],
            )
        except Exception as exc:
            LOGGER.exception("event_action.render_failed id=%s", row["id"])
            _record_run(db_path, row["id"], status="render_error", output=str(exc))
            return

        structured_output = bool(row["structured_output"])
        try:
            message_id = await asyncio.wait_for(
                dispatch_to_staff(
                    app_state=app_state,
                    workspace_id=event["workspace_id"],
                    topic_id=event["topic_id"],
                    staff=staff,
                    prompt_text=prompt,
                    sender="event",
                    raw_text=prompt,
                    event_action_id=row["id"] if structured_output else None,
                ),
                timeout=DISPATCH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _record_run(
                db_path,
                row["id"],
                status="dispatch_error",
                output=f"timeout after {DISPATCH_TIMEOUT_S:.0f}s",
            )
            return

        if structured_output:
            # last_run is written by the MQTT handler when the response arrives.
            LOGGER.info("event_action.structured_output_dispatched id=%s message_id=%s", row["id"], message_id)
        else:
            _record_run(
                db_path,
                row["id"],
                status="ok",
                output=f"message_id={message_id} prompt={prompt[:120]!r}",
            )
    except Exception as exc:
        LOGGER.exception("event_action.dispatch_failed id=%s", row["id"])
        _record_run(db_path, row["id"], status="dispatch_error", output=str(exc))


_GATE_RESPONSE_TIMEOUT_S = 60.0  # max wait for an LLM gate response


async def run_gate_actions(
    app_state,
    *,
    topic_id: str,
    workspace_id: str,
    variables: dict[str, str],
) -> bool:
    """Run before+structured_output gate actions synchronously and return whether to proceed.

    Each gate action dispatches a prompt to its staff and waits up to
    _GATE_RESPONSE_TIMEOUT_S for the structured JSON response via MQTT.
    Returns False if any gate action responds with {"break": true}; True otherwise.
    Gate futures are stored on app_state.gate_futures keyed by the prompt message_id
    and resolved by _resolve_gate_future in mqtt_client when the reply arrives.
    """
    from .dispatch import dispatch_to_staff

    conn = get_connection(app_state.db_path)
    try:
        topic_rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='topic' AND scope_id=?"
            "   AND event_type='topic_message_sent'"
            "   AND timing='before'"
            "   AND structured_output=1"
            "   AND enabled=1",
            (topic_id,),
        ).fetchall()
        workspace_rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='workspace' AND scope_id=?"
            "   AND event_type='topic_message_sent'"
            "   AND timing='before'"
            "   AND structured_output=1"
            "   AND enabled=1",
            (workspace_id,),
        ).fetchall()
        rows = list(topic_rows) + list(workspace_rows)
        topic_row = conn.execute(
            "SELECT t.id, t.subject, t.workspace_id, w.name AS workspace_name"
            " FROM topics t JOIN workspaces w ON w.id = t.workspace_id"
            " WHERE t.id = ?",
            (topic_id,),
        ).fetchone()
    finally:
        conn.close()

    if not rows:
        return True

    loop = asyncio.get_running_loop()
    gate_futures: dict = getattr(app_state, "gate_futures", {})

    async def _run_one(row) -> bool:
        conn2 = get_connection(app_state.db_path)
        try:
            staff = resolve_staff(conn2, row["staff_name"], workspace_id, topic_id)
        finally:
            conn2.close()

        if staff is None:
            LOGGER.warning("gate_action.staff_missing id=%s staff=%s", row["id"], row["staff_name"])
            _record_run(
                app_state.db_path, row["id"],
                status="staff_missing",
                output=f"staff_name={row['staff_name']!r} not resolvable at fire time",
            )
            return True  # missing staff → don't block

        merged = dict(variables)
        if topic_row:
            merged.setdefault("topic_json", json.dumps({
                "id": topic_row["id"],
                "subject": topic_row["subject"],
                "workspace_id": topic_row["workspace_id"],
                "workspace_name": topic_row["workspace_name"],
            }))

        try:
            prompt = render_template(
                row["prompt_template"], merged,
                db_path=app_state.db_path, workspace_id=workspace_id,
            )
        except Exception as exc:
            LOGGER.exception("gate_action.render_failed id=%s", row["id"])
            _record_run(app_state.db_path, row["id"], status="render_error", output=str(exc))
            return True

        fut: asyncio.Future = loop.create_future()

        try:
            message_id = await asyncio.wait_for(
                dispatch_to_staff(
                    app_state=app_state,
                    workspace_id=workspace_id,
                    topic_id=topic_id,
                    staff=staff,
                    prompt_text=prompt,
                    sender="event",
                    raw_text=prompt,
                    event_action_id=row["id"],
                ),
                timeout=DISPATCH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _record_run(
                app_state.db_path, row["id"],
                status="dispatch_error",
                output=f"dispatch timeout after {DISPATCH_TIMEOUT_S:.0f}s",
            )
            return True

        gate_futures[message_id] = fut
        try:
            result = await asyncio.wait_for(
                asyncio.shield(fut), timeout=_GATE_RESPONSE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            LOGGER.warning(
                "gate_action.response_timeout id=%s message_id=%s", row["id"], message_id
            )
            _record_run(
                app_state.db_path, row["id"],
                status="dispatch_error",
                output=f"gate response timeout after {_GATE_RESPONSE_TIMEOUT_S:.0f}s",
            )
            return True  # timeout → don't block
        finally:
            gate_futures.pop(message_id, None)

        return result != "break"

    results = await asyncio.gather(*(_run_one(row) for row in rows), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            LOGGER.exception("gate_action.unexpected_error: %r", r)
            continue
        if r is False:
            return False
    return True


def _record_run(db_path: str, action_id: str, *, status: str, output: str) -> None:
    """Write last_run_at / last_run_status / last_run_output for a dispatch attempt."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE event_actions"
            "   SET last_run_at=?, last_run_status=?, last_run_output=?"
            " WHERE id=?",
            (_now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), status, output[:4096], action_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Veto dispatch — synchronous-await path for pre-commit interceptors ────────

async def veto_dispatch(
    *,
    app_state,
    workspace_id: str,
    topic_id: str,
    variables: dict[str, str],
) -> VetoResult:
    """Dispatch all enabled topic_archiving actions and await their verdicts.

    Returns VetoResult(allowed, reason, timed_out):
    - allowed=True, timed_out=False  → proceed with archive
    - allowed=False, timed_out=False → at least one action denied; reason carries the text
    - allowed=True,  timed_out=True  → no verdict received within VETO_TIMEOUT_S
    - allowed=False, timed_out=True  → (not used; timeout is always returned as allowed=True)

    Caller maps timed_out=True to HTTP 504 and allowed=False to HTTP 423.
    """
    from .dispatch import dispatch_to_staff  # local to avoid circular import at module load

    conn = get_connection(app_state.db_path)
    try:
        topic_rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='topic'"
            "   AND scope_id=?"
            "   AND event_type='topic_archiving'"
            "   AND enabled=1",
            (topic_id,),
        ).fetchall()
        workspace_rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='workspace'"
            "   AND scope_id=?"
            "   AND event_type='topic_archiving'"
            "   AND enabled=1",
            (workspace_id,),
        ).fetchall()
        rows = list(topic_rows) + list(workspace_rows)
    finally:
        conn.close()

    if not rows:
        return VetoResult(allowed=True, reason="", timed_out=False)

    loop = asyncio.get_running_loop()
    # message_id → (Future, action_row)
    pending: dict[str, tuple[asyncio.Future, object]] = {}

    for row in rows:
        conn = get_connection(app_state.db_path)
        try:
            staff = resolve_staff(conn, row["staff_name"], workspace_id, topic_id)
        finally:
            conn.close()

        if staff is None:
            LOGGER.warning("veto_dispatch.staff_missing id=%s staff=%s", row["id"], row["staff_name"])
            _record_run(
                app_state.db_path,
                row["id"],
                status="staff_missing",
                output=f"staff_name={row['staff_name']!r} not resolvable at fire time",
            )
            continue

        try:
            prompt = render_template(
                row["prompt_template"], variables,
                db_path=app_state.db_path, workspace_id=workspace_id,
            )
        except Exception as exc:
            LOGGER.exception("veto_dispatch.render_failed id=%s", row["id"])
            _record_run(app_state.db_path, row["id"], status="render_error", output=str(exc))
            continue

        try:
            message_id = await asyncio.wait_for(
                dispatch_to_staff(
                    app_state=app_state,
                    workspace_id=workspace_id,
                    topic_id=topic_id,
                    staff=staff,
                    prompt_text=prompt,
                    sender="event",
                    raw_text=prompt,
                    response_mode="verdict",
                ),
                timeout=DISPATCH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            LOGGER.warning("veto_dispatch.dispatch_timeout id=%s", row["id"])
            _record_run(
                app_state.db_path,
                row["id"],
                status="dispatch_error",
                output=f"timeout after {DISPATCH_TIMEOUT_S:.0f}s",
            )
            continue

        fut: asyncio.Future = loop.create_future()
        app_state.veto_futures[message_id] = fut
        pending[message_id] = (fut, row)

    if not pending:
        return VetoResult(allowed=True, reason="", timed_out=False)

    futures_list = [fut for fut, _row in pending.values()]
    try:
        done, timed_out_set = await asyncio.wait(futures_list, timeout=VETO_TIMEOUT_S)
    finally:
        for mid in list(pending):
            app_state.veto_futures.pop(mid, None)

    if timed_out_set:
        LOGGER.warning(
            "veto_dispatch.timeout topic_id=%s pending=%d",
            topic_id,
            len(timed_out_set),
        )
        for _mid, (fut, row) in pending.items():
            if fut in timed_out_set:
                _record_run(app_state.db_path, row["id"], status="veto_timeout", output="no verdict received")
        return VetoResult(allowed=True, reason="", timed_out=True)

    # Scan completed futures; first deny wins
    for _mid, (fut, row) in pending.items():
        if fut not in done:
            continue
        if fut.exception():
            LOGGER.warning("veto_dispatch.future_exception mid=%s", _mid)
            continue
        verdict_data = fut.result()
        if isinstance(verdict_data, dict):
            verdict = verdict_data.get("verdict")
            reason = verdict_data.get("reason", "")
            agent_name = verdict_data.get("agent_name", "")
            if verdict == "deny":
                _record_run(
                    app_state.db_path,
                    row["id"],
                    status="vetoed",
                    output=f"agent={agent_name!r} reason={reason[:200]!r}",
                )
                LOGGER.info(
                    "veto_dispatch.denied topic_id=%s agent=%s reason=%s",
                    topic_id,
                    agent_name,
                    reason[:120],
                )
                return VetoResult(allowed=False, reason=reason, timed_out=False)
            _record_run(
                app_state.db_path,
                row["id"],
                status="ok",
                output=f"agent={agent_name!r} verdict=allow",
            )

    return VetoResult(allowed=True, reason="", timed_out=False)


# ── Stall watchdog ────────────────────────────────────────────────────────────

async def worker_watchdog(
    app_state,
    *,
    check_interval: float = 30.0,
    idle_threshold: float = 60.0,
) -> None:
    """Observation-only: log when the worker has made no progress for more than
    `idle_threshold` seconds while the queue is non-empty. Never cancels or restarts the
    worker. The non-empty gate avoids spurious warnings during normal idle periods.

    Production callers leave the defaults (30 s / 60 s); tests override both to keep
    watchdog assertions sub-second.
    """
    while True:
        await asyncio.sleep(check_interval)
        last = getattr(app_state, "event_worker_last_progress", None)
        if last is None:
            continue
        idle = (_now_utc() - last).total_seconds()
        qsize = app_state.event_queue.qsize()
        if idle > idle_threshold and qsize > 0:
            LOGGER.warning(
                "event_worker.stalled idle_for=%ds qsize=%d",
                int(idle),
                qsize,
            )
