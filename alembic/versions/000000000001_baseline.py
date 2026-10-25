"""Baseline schema — all 13 tables at the post-migration state.

This revision represents the final schema produced by _prebaseline.py
(init_db_sqlite + all _MIGRATIONS + all named migration helpers) expressed
as SQLAlchemy Core table definitions.  Fresh installs apply only this
revision; existing installs that already ran the legacy init are stamped
at this revision without re-running DDL.

Revision ID: 000000000001
Revises:
Create Date: 2026-10-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "000000000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("repo_url", sa.Text(), nullable=False),
        sa.Column("container_name", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text()),
        sa.Column("last_refreshed_at", sa.Text()),
        sa.Column("last_message_at", sa.Text()),
        sa.Column("last_dispatched_at", sa.Text()),
        sa.Column("last_responded_at", sa.Text()),
        sa.Column("last_agent_state", sa.Text()),
        sa.Column("repo_ref", sa.Text(), nullable=False, server_default="master"),
    )
    # MySQL has no partial indexes; phase 2 converts this to a functional index
    # over a generated column per the design doc §6 MySQL partial-index conversion.
    op.create_index(
        "idx_workspaces_active_name",
        "workspaces",
        ["name"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "staffs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scope_type", sa.Text(),
            sa.CheckConstraint(
                "scope_type IN ('global', 'workspace', 'topic')",
                name="ck_staffs_scope_type",
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("adapter", sa.Text(), nullable=False, server_default="claude-code"),
        sa.Column("model", sa.Text()),
        sa.Column("system_prompt", sa.Text()),
        sa.Column("agent", sa.Text()),
        sa.Column(
            "session_scope", sa.Text(),
            sa.CheckConstraint(
                "session_scope IN ('topic', 'workspace', 'global', 'none')",
                name="ck_staffs_session_scope",
            ),
            nullable=False,
            server_default="topic",
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extra_flags", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    # Expression-based unique index: COALESCE(scope_id, '') makes NULL scope_ids comparable.
    op.create_index(
        "idx_staffs_scope_name",
        "staffs",
        [sa.text("scope_type"), sa.text("COALESCE(scope_id, '')"), sa.text("name")],
        unique=True,
    )

    op.create_table(
        "staff_sessions",
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("staff_name", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_type", "scope_id", "staff_name",
                                name="pk_staff_sessions"),
    )

    op.create_table(
        "config",
        sa.Column(
            "scope_type", sa.Text(),
            sa.CheckConstraint(
                "scope_type IN ('global', 'workspace')",
                name="ck_config_scope_type",
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_type", "scope_id", "key", name="pk_config"),
    )

    op.create_table(
        "topics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36),
                  sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("branch_name", sa.Text(), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text()),
        sa.Column("repo_ref", sa.Text()),
        sa.Column("base_sha", sa.Text()),
        sa.Column("veto_status", sa.Text()),
        sa.Column("veto_reason", sa.Text()),
        sa.Column("current_staff_name", sa.Text()),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic_id", sa.String(36),
                  sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("adapter", sa.Text(), nullable=False),
        sa.Column("llm_session_id", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_sessions_topic_agent",
        "sessions",
        ["topic_id", "agent_name"],
        unique=True,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic_id", sa.String(36),
                  sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("transcript", sa.Text()),
        sa.Column("usage_json", sa.Text()),
        sa.Column("attachments_json", sa.Text()),
        sa.Column("event_action_id", sa.Text()),
        sa.Column("silent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("interrupt_reason", sa.Text()),
        sa.Column("sender_kind", sa.Text()),
        sa.Column("sender_name", sa.Text()),
        sa.Column("receiver_kind", sa.Text()),
        sa.Column("receiver_name", sa.Text()),
        sa.Column("task_id", sa.Text()),
        sa.Column("reply_to_message_id", sa.Text()),
        sa.Column("dispatch_token", sa.Text()),
    )
    op.create_index("idx_messages_task", "messages", ["task_id", "created_at"])
    op.create_index("idx_messages_reply_to", "messages", ["reply_to_message_id"])
    # MySQL has no partial indexes; phase 2 converts this to a functional index
    # over a generated column per the design doc §6 MySQL partial-index conversion.
    op.create_index(
        "idx_messages_envelope_backfill",
        "messages",
        ["id"],
        sqlite_where=sa.text("sender_kind IS NULL"),
        postgresql_where=sa.text("sender_kind IS NULL"),
    )

    op.create_table(
        "attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36),
                  sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("topic_id", sa.String(36),
                  sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column(
            "direction", sa.Text(),
            sa.CheckConstraint("direction IN ('inbound', 'outbound')",
                               name="ck_attachments_direction"),
            nullable=False,
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text()),
        # created_at is a Unix timestamp (float).  The server_default was
        # previously unixepoch('now','subsec') which is SQLite-only.  The
        # design (§6) moves this default into Python: every INSERT site
        # now provides the value explicitly via time.time().
        sa.Column("created_at", sa.Float(), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("idx_chunks_message", "chunks", ["message_id", "seq"])
    op.create_index("idx_chunks_topic", "chunks", ["topic_id"])
    op.create_index("idx_chunks_created", "chunks", ["created_at"])

    op.create_table(
        "event_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_type", sa.Text(),
            sa.CheckConstraint(
                "event_type IN ('topic_message_sent', 'topic_message_received',"
                " 'topic_scheduler', 'topic_archived', 'topic_archiving')",
                name="ck_event_actions_event_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "scope_type", sa.Text(),
            sa.CheckConstraint("scope_type IN ('topic', 'workspace')",
                               name="ck_event_actions_scope_type"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("staff_name", sa.Text(), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column(
            "timing", sa.Text(),
            sa.CheckConstraint("timing IN ('before', 'after')",
                               name="ck_event_actions_timing"),
        ),
        sa.Column("cron_expr", sa.Text()),
        sa.Column("last_fired_at", sa.Text()),
        sa.Column("last_run_at", sa.Text()),
        sa.Column(
            "last_run_status", sa.Text(),
            sa.CheckConstraint(
                "last_run_status IN ('ok', 'staff_missing', 'render_error',"
                " 'dispatch_error', 'vetoed', 'veto_timeout')",
                name="ck_event_actions_last_run_status",
            ),
        ),
        sa.Column("last_run_output", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("structured_output", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("silent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(event_type = 'topic_scheduler' AND cron_expr IS NOT NULL AND timing IS NULL)"
            " OR (event_type = 'topic_message_sent' AND cron_expr IS NULL"
            "     AND timing IN ('before', 'after'))"
            " OR (event_type IN ('topic_message_received', 'topic_archived', 'topic_archiving')"
            "     AND cron_expr IS NULL AND (timing IS NULL OR timing = 'after'))",
            name="ck_event_actions_schedule",
        ),
    )
    op.create_index(
        "idx_event_actions_scope_event",
        "event_actions",
        ["scope_type", "scope_id", "event_type", "enabled"],
    )
    # MySQL has no partial indexes; phase 2 converts this to a functional index
    # over a generated column per the design doc §6 MySQL partial-index conversion.
    op.create_index(
        "idx_event_actions_scheduler",
        "event_actions",
        ["event_type", "enabled"],
        sqlite_where=sa.text("event_type = 'topic_scheduler'"),
        postgresql_where=sa.text("event_type = 'topic_scheduler'"),
    )

    op.create_table(
        "notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scope_type", sa.Text(),
            sa.CheckConstraint("scope_type IN ('workspace', 'topic')",
                               name="ck_notes_scope_type"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("tags", sa.Text(), nullable=False, server_default="'[]'"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("scope_type", "scope_id", "key", name="uq_notes_scope_key"),
    )
    op.create_index("idx_notes_scope", "notes", ["scope_type", "scope_id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic_id", sa.String(36),
                  sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("root_task_id", sa.Text(), nullable=False),
        sa.Column("parent_task_id", sa.Text()),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column(
            "dispatcher_kind", sa.Text(),
            sa.CheckConstraint("dispatcher_kind IN ('user', 'staff')",
                               name="ck_tasks_dispatcher_kind"),
            nullable=False,
        ),
        sa.Column("dispatcher_name", sa.Text()),
        sa.Column("assignee_name", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.Text()),
        sa.Column(
            "state", sa.Text(),
            sa.CheckConstraint(
                "state IN ('submitted', 'working', 'input-required',"
                " 'completed', 'failed', 'escalated')",
                name="ck_tasks_state",
            ),
            nullable=False,
        ),
        sa.Column("failure_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("result_summary", sa.Text()),
        sa.Column("result_artifacts", sa.Text()),
        sa.Column("result_status", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.Text()),
    )
    op.create_index("idx_tasks_topic", "tasks", ["topic_id"])
    op.create_index("idx_tasks_root", "tasks", ["root_task_id"])
    op.create_index("idx_tasks_topic_state", "tasks", ["topic_id", "state"])
    op.create_index("idx_tasks_parent", "tasks", ["parent_task_id"])

    op.create_table(
        "staged_dispatches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prompt_message_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=False),
        sa.Column("sender_name", sa.Text(), nullable=False),
        sa.Column("receiver_kind", sa.Text(), nullable=False),
        sa.Column("receiver_name", sa.Text()),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column(
            "kind", sa.Text(),
            sa.CheckConstraint("kind IN ('question', 'answer', 'result_ready')",
                               name="ck_staged_dispatches_kind"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("dispatched_at", sa.Text()),
    )
    op.create_index(
        "idx_staged_dispatches_prompt", "staged_dispatches", ["prompt_message_id"]
    )
    # MySQL has no partial indexes; phase 2 converts this to a functional index
    # over a generated column per the design doc §6 MySQL partial-index conversion.
    op.create_index(
        "idx_staged_dispatches_undispatched",
        "staged_dispatches",
        ["dispatched_at"],
        sqlite_where=sa.text("dispatched_at IS NULL"),
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("staged_dispatches")
    op.drop_table("tasks")
    op.drop_table("notes")
    op.drop_table("event_actions")
    op.drop_table("chunks")
    op.drop_table("attachments")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("topics")
    op.drop_table("config")
    op.drop_table("staff_sessions")
    op.drop_table("staffs")
    op.drop_table("workspaces")
