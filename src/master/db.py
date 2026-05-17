from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    repo_url          TEXT NOT NULL,
    container_name    TEXT,
    created_at        TEXT NOT NULL,
    archived_at       TEXT,
    last_refreshed_at TEXT,
    last_message_at   TEXT,
    last_dispatched_at TEXT,
    last_responded_at  TEXT
);

CREATE TABLE IF NOT EXISTS staffs (
    id            TEXT PRIMARY KEY,
    scope_type    TEXT NOT NULL CHECK (scope_type IN ('global', 'workspace', 'topic')),
    scope_id      TEXT,
    name          TEXT NOT NULL,
    adapter       TEXT NOT NULL DEFAULT 'claude-code',
    model         TEXT,
    system_prompt TEXT,
    agent         TEXT,
    session_scope TEXT NOT NULL DEFAULT 'topic' CHECK (session_scope IN ('topic', 'workspace', 'global', 'none')),
    is_default    INTEGER NOT NULL DEFAULT 0,
    extra_flags   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_sessions (
    scope_type  TEXT NOT NULL,
    scope_id    TEXT NOT NULL DEFAULT '',
    staff_name  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_id, staff_name)
);

CREATE TABLE IF NOT EXISTS config (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'workspace')),
    scope_id   TEXT NOT NULL DEFAULT '',
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_id, key)
);

CREATE TABLE IF NOT EXISTS topics (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
    subject       TEXT NOT NULL,
    branch_name   TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    archived_at   TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    topic_id       TEXT NOT NULL REFERENCES topics(id),
    agent_name     TEXT NOT NULL,
    adapter        TEXT NOT NULL,
    llm_session_id TEXT,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    topic_id         TEXT NOT NULL REFERENCES topics(id),
    sender           TEXT NOT NULL,
    agent_name       TEXT,
    text             TEXT NOT NULL,
    transcript       TEXT,
    usage_json       TEXT,
    attachments_json TEXT,
    event_action_id  TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id),
    topic_id    TEXT NOT NULL REFERENCES topics(id),
    filename    TEXT NOT NULL,
    mime_type   TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    storage_uri TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    direction   TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL,
    topic_id    TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event       TEXT NOT NULL,
    agent_name  TEXT,
    created_at  REAL NOT NULL DEFAULT (unixepoch('now','subsec'))
);
CREATE INDEX IF NOT EXISTS idx_chunks_message ON chunks (message_id, seq);
CREATE INDEX IF NOT EXISTS idx_chunks_topic   ON chunks (topic_id);
CREATE INDEX IF NOT EXISTS idx_chunks_created ON chunks (created_at);

CREATE TABLE IF NOT EXISTS event_actions (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL CHECK (event_type IN (
                        'topic_message_sent',
                        'topic_message_received',
                        'topic_scheduler',
                        'topic_archived',
                        'topic_archiving'
                    )),
    scope_type      TEXT NOT NULL CHECK (scope_type IN ('topic', 'workspace')),
    scope_id        TEXT NOT NULL,
    staff_name      TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    timing          TEXT CHECK (timing IN ('before', 'after')),
    cron_expr       TEXT,
    last_fired_at   TEXT,
    last_run_at     TEXT,
    last_run_status TEXT CHECK (last_run_status IN (
                        'ok',
                        'staff_missing',
                        'render_error',
                        'dispatch_error',
                        'vetoed',
                        'veto_timeout'
                    )),
    last_run_output   TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    structured_output INTEGER NOT NULL DEFAULT 0 CHECK (structured_output IN (0, 1)),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    CHECK (
        (event_type = 'topic_scheduler'      AND cron_expr IS NOT NULL AND timing IS NULL)
        OR
        (event_type = 'topic_message_sent'   AND cron_expr IS NULL     AND timing IN ('before','after'))
        OR
        (event_type IN ('topic_message_received','topic_archived','topic_archiving')
                                              AND cron_expr IS NULL     AND (timing IS NULL OR timing = 'after'))
    )
);
CREATE INDEX IF NOT EXISTS idx_event_actions_scope_event
    ON event_actions (scope_type, scope_id, event_type, enabled);
CREATE INDEX IF NOT EXISTS idx_event_actions_scheduler
    ON event_actions (event_type, enabled) WHERE event_type = 'topic_scheduler';

CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    scope_type  TEXT NOT NULL CHECK (scope_type IN ('workspace', 'topic')),
    scope_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (scope_type, scope_id, key)
);
CREATE INDEX IF NOT EXISTS idx_notes_scope ON notes (scope_type, scope_id);
"""

TABLES = ["workspaces", "staffs", "staff_sessions", "config", "topics", "sessions", "messages", "attachments", "chunks", "event_actions", "notes"]


_MIGRATIONS = [
    "ALTER TABLE workspaces ADD COLUMN archived_at TEXT",
    "ALTER TABLE topics ADD COLUMN archived_at TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_refreshed_at TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_message_at TEXT",
    "ALTER TABLE messages ADD COLUMN usage_json TEXT",
    "ALTER TABLE topics ADD COLUMN repo_ref TEXT",
    "ALTER TABLE topics ADD COLUMN base_sha TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_dispatched_at TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_responded_at TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_agent_state TEXT",
    "ALTER TABLE chunks ADD COLUMN agent_name TEXT",
    "ALTER TABLE event_actions ADD COLUMN structured_output INTEGER DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN event_action_id TEXT",
    "ALTER TABLE topics ADD COLUMN veto_status TEXT",
    "ALTER TABLE topics ADD COLUMN veto_reason TEXT",
    "ALTER TABLE topics ADD COLUMN current_staff_name TEXT",
    "ALTER TABLE messages ADD COLUMN interrupt_reason TEXT",
]


def _migrate_workspace_name_uniqueness(conn: sqlite3.Connection) -> None:
    """Replace the table-level UNIQUE on workspaces.name with a partial index
    so that archived workspaces do not block reuse of their name."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='workspaces'"
    ).fetchone()
    if row is None:
        return
    table_sql: str = row[0] or ""
    if "UNIQUE" not in table_sql:
        # Constraint already removed — just ensure the partial index exists.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_active_name"
            " ON workspaces(name) WHERE archived_at IS NULL"
        )
        conn.commit()
        return
    # SQLite cannot drop column constraints; recreate the table without it.
    LOGGER.info("db.migration_start relax_workspace_name_unique")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workspaces_new (
            id             TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            repo_url       TEXT NOT NULL,
            container_name TEXT,
            created_at     TEXT NOT NULL,
            archived_at    TEXT
        );
        INSERT OR IGNORE INTO workspaces_new
            SELECT id, name, repo_url, container_name, created_at, archived_at
            FROM workspaces;
        DROP TABLE workspaces;
        ALTER TABLE workspaces_new RENAME TO workspaces;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_active_name
            ON workspaces(name) WHERE archived_at IS NULL;
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    LOGGER.info("db.migration_done relax_workspace_name_unique")


def _migrate_agents_to_staffs(conn: sqlite3.Connection) -> None:
    """Migrate workspace_agents rows into the staffs table (one-time, idempotent)."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_agents'"
    ).fetchone() is None:
        return
    if conn.execute("SELECT 1 FROM staffs LIMIT 1").fetchone() is not None:
        return
    rows = conn.execute(
        "SELECT id, workspace_id, agent_name, adapter, subagent, created_at"
        " FROM workspace_agents WHERE active = 1"
    ).fetchall()
    if not rows:
        return
    LOGGER.info("db.migration_start agents_to_staffs count=%d", len(rows))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in rows:
        is_default = 1 if r[2] == "claude" else 0
        conn.execute(
            "INSERT OR IGNORE INTO staffs"
            " (id, scope_type, scope_id, name, adapter, model, system_prompt, agent,"
            "  session_scope, is_default, extra_flags, created_at, updated_at)"
            " VALUES (?, 'workspace', ?, ?, ?, NULL, NULL, ?, 'topic', ?, NULL, ?, ?)",
            (r[0], r[1], r[2], r[3], r[4], is_default, r[5], now),
        )
    conn.commit()
    LOGGER.info("db.migration_done agents_to_staffs")


def _migrate_event_actions_v2(conn: sqlite3.Connection) -> None:
    """Add topic_archiving event_type and vetoed/veto_timeout last_run_status values.

    SQLite cannot modify CHECK constraints in-place; we recreate the table.
    Idempotent: skips if topic_archiving is already present in the schema.

    Preserves every column added by prior `_MIGRATIONS` (e.g. `structured_output`)
    by reading the legacy column list dynamically rather than hard-coding it. An
    earlier form of this helper hard-coded the SELECT list and silently dropped
    `structured_output` on upgrade.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='event_actions'"
    ).fetchone()
    if row is None or "topic_archiving" in (row[0] or ""):
        return
    # Connection from init_db() has no row_factory set; PRAGMA table_info
    # returns positional tuples (cid, name, type, notnull, dflt_value, pk).
    legacy_cols = {r[1] for r in conn.execute("PRAGMA table_info(event_actions)")}
    structured_output_select = (
        "structured_output" if "structured_output" in legacy_cols else "0 AS structured_output"
    )
    LOGGER.info("db.migration_start event_actions_v2")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS event_actions_new (
            id              TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL CHECK (event_type IN (
                                'topic_message_sent',
                                'topic_message_received',
                                'topic_scheduler',
                                'topic_archived',
                                'topic_archiving'
                            )),
            scope_type      TEXT NOT NULL CHECK (scope_type IN ('topic')),
            scope_id        TEXT NOT NULL,
            staff_name      TEXT NOT NULL,
            prompt_template TEXT NOT NULL,
            timing          TEXT CHECK (timing IN ('before', 'after')),
            cron_expr       TEXT,
            last_fired_at   TEXT,
            last_run_at     TEXT,
            last_run_status TEXT CHECK (last_run_status IN (
                                'ok',
                                'staff_missing',
                                'render_error',
                                'dispatch_error',
                                'vetoed',
                                'veto_timeout'
                            )),
            last_run_output TEXT,
            enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            structured_output INTEGER NOT NULL DEFAULT 0 CHECK (structured_output IN (0, 1)),
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            CHECK (
                (event_type = 'topic_scheduler'      AND cron_expr IS NOT NULL AND timing IS NULL)
                OR
                (event_type = 'topic_message_sent'   AND cron_expr IS NULL     AND timing IN ('before','after'))
                OR
                (event_type IN ('topic_message_received','topic_archived','topic_archiving')
                                                      AND cron_expr IS NULL     AND (timing IS NULL OR timing = 'after'))
            )
        );
        INSERT OR IGNORE INTO event_actions_new (
            id, event_type, scope_type, scope_id, staff_name, prompt_template,
            timing, cron_expr, last_fired_at, last_run_at, last_run_status,
            last_run_output, enabled, structured_output, created_at, updated_at
        )
            SELECT id, event_type, scope_type, scope_id, staff_name, prompt_template,
                   timing, cron_expr, last_fired_at, last_run_at, last_run_status,
                   last_run_output, enabled, {structured_output_select}, created_at, updated_at
            FROM event_actions;
        DROP TABLE event_actions;
        ALTER TABLE event_actions_new RENAME TO event_actions;
        CREATE INDEX IF NOT EXISTS idx_event_actions_scope_event
            ON event_actions (scope_type, scope_id, event_type, enabled);
        CREATE INDEX IF NOT EXISTS idx_event_actions_scheduler
            ON event_actions (event_type, enabled) WHERE event_type = 'topic_scheduler';
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    LOGGER.info("db.migration_done event_actions_v2")


def _migrate_event_actions_v3(conn: sqlite3.Connection) -> None:
    """Widen event_actions scope_type CHECK constraint to allow 'workspace'.

    SQLite cannot modify CHECK constraints in-place; we recreate the table.
    Idempotent: skips if 'workspace' is already present in the constraint.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='event_actions'"
    ).fetchone()
    if row is None or "'workspace'" in (row[0] or ""):
        return
    LOGGER.info("db.migration_start event_actions_v3")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS event_actions_new (
            id              TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL CHECK (event_type IN (
                                'topic_message_sent',
                                'topic_message_received',
                                'topic_scheduler',
                                'topic_archived',
                                'topic_archiving'
                            )),
            scope_type      TEXT NOT NULL CHECK (scope_type IN ('topic', 'workspace')),
            scope_id        TEXT NOT NULL,
            staff_name      TEXT NOT NULL,
            prompt_template TEXT NOT NULL,
            timing          TEXT CHECK (timing IN ('before', 'after')),
            cron_expr       TEXT,
            last_fired_at   TEXT,
            last_run_at     TEXT,
            last_run_status TEXT CHECK (last_run_status IN (
                                'ok',
                                'staff_missing',
                                'render_error',
                                'dispatch_error',
                                'vetoed',
                                'veto_timeout'
                            )),
            last_run_output   TEXT,
            enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            structured_output INTEGER NOT NULL DEFAULT 0 CHECK (structured_output IN (0, 1)),
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            CHECK (
                (event_type = 'topic_scheduler'      AND cron_expr IS NOT NULL AND timing IS NULL)
                OR
                (event_type = 'topic_message_sent'   AND cron_expr IS NULL     AND timing IN ('before','after'))
                OR
                (event_type IN ('topic_message_received','topic_archived','topic_archiving')
                                                      AND cron_expr IS NULL     AND (timing IS NULL OR timing = 'after'))
            )
        );
        INSERT OR IGNORE INTO event_actions_new
            SELECT * FROM event_actions;
        DROP TABLE event_actions;
        ALTER TABLE event_actions_new RENAME TO event_actions;
        CREATE INDEX IF NOT EXISTS idx_event_actions_scope_event
            ON event_actions (scope_type, scope_id, event_type, enabled);
        CREATE INDEX IF NOT EXISTS idx_event_actions_scheduler
            ON event_actions (event_type, enabled) WHERE event_type = 'topic_scheduler';
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    LOGGER.info("db.migration_done event_actions_v3")


def _migrate_staffs_session_scope_none(conn: sqlite3.Connection) -> None:
    """Widen the session_scope CHECK constraint to allow 'none'.

    SQLite cannot modify CHECK constraints in-place; we recreate the table.
    Idempotent: skips if 'none' is already present in the constraint.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='staffs'"
    ).fetchone()
    if row is None or "'none'" in (row[0] or ""):
        return
    LOGGER.info("db.migration_start staffs_session_scope_none")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS staffs_new (
            id            TEXT PRIMARY KEY,
            scope_type    TEXT NOT NULL CHECK (scope_type IN ('global', 'workspace', 'topic')),
            scope_id      TEXT,
            name          TEXT NOT NULL,
            adapter       TEXT NOT NULL DEFAULT 'claude-code',
            model         TEXT,
            system_prompt TEXT,
            agent         TEXT,
            session_scope TEXT NOT NULL DEFAULT 'topic'
                          CHECK (session_scope IN ('topic', 'workspace', 'global', 'none')),
            is_default    INTEGER NOT NULL DEFAULT 0,
            extra_flags   TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        INSERT INTO staffs_new SELECT * FROM staffs;
        DROP TABLE staffs;
        ALTER TABLE staffs_new RENAME TO staffs;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_staffs_scope_name
            ON staffs(scope_type, COALESCE(scope_id, ''), name);
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    LOGGER.info("db.migration_done staffs_session_scope_none")


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_staffs_scope_name"
            " ON staffs(scope_type, COALESCE(scope_id, ''), name)"
        )
        conn.commit()
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists
        _migrate_workspace_name_uniqueness(conn)
        _migrate_agents_to_staffs(conn)
        _migrate_event_actions_v2(conn)
        _migrate_event_actions_v3(conn)
        _migrate_staffs_session_scope_none(conn)
        conn.commit()
    finally:
        conn.close()


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_info(db_path: str) -> dict[str, list[str]]:
    conn = get_connection(db_path)
    try:
        result: dict[str, list[str]] = {}
        for table in TABLES:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            result[table] = [row["name"] for row in rows]
        return result
    finally:
        conn.close()
