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
    last_message_at   TEXT
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
    session_scope TEXT NOT NULL DEFAULT 'topic' CHECK (session_scope IN ('topic', 'workspace', 'global')),
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
"""

TABLES = ["workspaces", "staffs", "staff_sessions", "config", "topics", "sessions", "messages", "attachments"]


_MIGRATIONS = [
    "ALTER TABLE workspaces ADD COLUMN archived_at TEXT",
    "ALTER TABLE topics ADD COLUMN archived_at TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_refreshed_at TEXT",
    "ALTER TABLE workspaces ADD COLUMN last_message_at TEXT",
    "ALTER TABLE messages ADD COLUMN usage_json TEXT",
    "ALTER TABLE topics ADD COLUMN repo_ref TEXT",
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
