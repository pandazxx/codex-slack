"""Event dispatch infrastructure: queue, worker, watchdog, and emit_event helper.

A single asyncio.Queue is owned by app.state. Emit sites call emit_event() from
any thread (FastAPI loop, MQTT thread, scheduler thread) and return immediately.
The single async event_worker task drains the queue — one event at a time, FIFO
across events — calling _handle_event for each. Within a single event, all
matching event_actions fire concurrently via asyncio.gather so a slow sibling
does not delay its peers or the next queued event.

See ADR-0013 and design doc §4 for the full rationale.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .db import get_connection
from .staffs import resolve_staff

LOGGER = logging.getLogger(__name__)

# Wraps only the dispatch_to_staff call in _dispatch_one — i.e. the time to insert the
# message row, broadcast on the WS hub, and publish to MQTT. NOT a budget for the agent's
# LLM response, which is fully async (the agent reply arrives via MQTT minutes later).
DISPATCH_TIMEOUT_S = 10.0


# ── Template rendering ────────────────────────────────────────────────────────

class _SafeDict(dict):
    """dict subclass that returns the literal placeholder for missing keys.

    A misconfigured template logs a warning but does not crash dispatch.
    """
    def __missing__(self, key: str) -> str:
        LOGGER.warning("event_action.unknown_variable key=%s", key)
        return "{" + key + "}"


def render_template(template: str, variables: dict[str, str]) -> str:
    """Substitute {variable} placeholders; unknown placeholders are left literal."""
    return template.format_map(_SafeDict(variables))


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
    """Load matching event_actions and dispatch all of them in parallel."""
    conn = get_connection(app_state.db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM event_actions"
            " WHERE scope_type='topic'"
            "   AND scope_id=?"
            "   AND event_type=?"
            "   AND enabled=1"
            "   AND (timing IS NULL OR timing=?)",
            (event["topic_id"], event["event_type"], event["timing"]),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return

    await asyncio.gather(
        *(_dispatch_one(app_state, row, event) for row in rows),
        return_exceptions=True,
    )


async def _dispatch_one(app_state, row, event: dict) -> None:
    """Dispatch a single matching action, recording last_run_* on completion."""
    from .dispatch import dispatch_to_staff  # local import avoids circular deps at module load

    try:
        conn = get_connection(app_state.db_path)
        try:
            staff = resolve_staff(
                conn,
                row["staff_name"],
                event["workspace_id"],
                event["topic_id"],
            )
        finally:
            conn.close()

        if staff is None:
            LOGGER.warning("event_action.staff_missing id=%s staff=%s", row["id"], row["staff_name"])
            _record_run(
                app_state,
                row["id"],
                status="staff_missing",
                output=f"staff_name={row['staff_name']!r} not resolvable at fire time",
            )
            return

        try:
            prompt = render_template(row["prompt_template"], event["variables"])
        except Exception as exc:
            LOGGER.exception("event_action.render_failed id=%s", row["id"])
            _record_run(app_state, row["id"], status="render_error", output=str(exc))
            return

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
                ),
                timeout=DISPATCH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _record_run(
                app_state,
                row["id"],
                status="dispatch_error",
                output=f"timeout after {DISPATCH_TIMEOUT_S:.0f}s",
            )
            return

        _record_run(
            app_state,
            row["id"],
            status="ok",
            output=f"message_id={message_id} prompt={prompt[:120]!r}",
        )
    except Exception as exc:
        LOGGER.exception("event_action.dispatch_failed id=%s", row["id"])
        _record_run(app_state, row["id"], status="dispatch_error", output=str(exc))


def _record_run(app_state, action_id: str, *, status: str, output: str) -> None:
    """Write last_run_at / last_run_status / last_run_output for a dispatch attempt."""
    conn = get_connection(app_state.db_path)
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
