"""Orchestration validator and constants for the agent-communication protocol.

This module is pure (no DB, no I/O) so it is trivially testable in isolation.
Every dispatch that carries explicit sender/receiver metadata must be checked
here before it is written to the database.

Communication matrix (design §1 — phase-a-relevant rows):
  user  → staff                allowed
  staff → staff                allowed only when a task_id is provided (inside a task)
  staff → user                 allowed only when dispatcher_kind='user' or no task context
                               (normal reply-to-sender at depth 0), rejected when the task's
                               dispatcher is staff and task state != 'escalated'
  staff → staff(self)          rejected (self_delegation)
  staff → staff without task   rejected (cold_outreach)
  system → any                 allowed (master-generated messages)
"""
from __future__ import annotations

from typing import Protocol

MAX_DELEGATION_DEPTH: int = 1
MAX_TASKS_PER_ROOT: int = 20

_VALID_KINDS = frozenset({"user", "staff", "system"})


class AncestorRow(Protocol):
    """Structural type for a task ancestor row returned from the DB.

    Any object with string-keyed item access (sqlite3.Row, dict, etc.) qualifies.
    """
    def __getitem__(self, key: str) -> object: ...


def detect_cycle(
    ancestor_rows: list[AncestorRow],
    proposed_assignee: str,
) -> bool:
    """Return True if proposed_assignee appears in any ancestor task row.

    Each row must expose ``assignee_name`` and ``dispatcher_name`` via item
    access.  The caller supplies the already-fetched ancestor chain (walking
    parent_task_id upward) so this function stays pure and testable without a
    DB connection.

    Returns True  → cycle detected; caller should reject with 'cycle_detected'.
    Returns False → no cycle.
    """
    for row in ancestor_rows:
        if proposed_assignee in (row["assignee_name"], row["dispatcher_name"]):
            return True
    return False


def validate_envelope(
    sender_kind: str,
    sender_name: str | None,
    receiver_kind: str,
    receiver_name: str | None,
    task_id: str | None = None,
    dispatcher_kind: str | None = None,
) -> None:
    """Validate a sender→receiver pair against the communication matrix.

    ``dispatcher_kind`` is the kind of the task's dispatcher (the entity that
    owns the task the sender is currently working on).  Pass it when the
    sender is staff and the call is within a task context so the staff→user
    rule can be applied correctly:

      - No task context (task_id is None) or dispatcher_kind='user': staff may
        reply to the user freely — this is the normal depth-0 reply path.
      - Task context where dispatcher_kind='staff': staff→user is only allowed
        when the task is in 'escalated' state.  Without the task state this
        function conservatively rejects it; callers that know the state is
        'escalated' should pass dispatcher_kind='user' or omit dispatcher_kind.

    Raises ValueError with a machine-readable code as the message on rejection:
      'self_delegation'       — staff delegating to itself
      'cold_outreach'         — staff-to-staff without an active task context
      'invalid_sender'        — unknown sender_kind
      'invalid_receiver'      — unknown receiver_kind
      'invalid_staff_to_user' — staff→user rejected because task dispatcher is
                                staff and task is not in escalated state

    On success returns None.
    """
    if sender_kind not in _VALID_KINDS:
        raise ValueError("invalid_sender")
    if receiver_kind not in _VALID_KINDS:
        raise ValueError("invalid_receiver")

    if sender_kind == "system":
        return

    if sender_kind == "user":
        return

    if sender_kind == "staff":
        if receiver_kind == "user":
            # Allowed when:
            #   (a) no task context at all (depth-0 reply to user), or
            #   (b) task's dispatcher is the user (depth-0 task set up by user)
            # Rejected when the task was dispatched by staff and the task is
            # not escalated (the direct staff→user escalation channel is not
            # open in that case).
            if task_id and dispatcher_kind == "staff":
                raise ValueError("invalid_staff_to_user")
            return
        if receiver_kind == "staff":
            if sender_name and receiver_name and sender_name == receiver_name:
                raise ValueError("self_delegation")
            if not task_id:
                raise ValueError("cold_outreach")
            return
        if receiver_kind == "system":
            return
