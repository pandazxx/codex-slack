from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    repo_url       TEXT NOT NULL,
    container_name TEXT,
    created_at     TEXT NOT NULL,
    archived_at    TEXT
);

CREATE TABLE IF NOT EXISTS workspace_agents (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    agent_name   TEXT NOT NULL,
    adapter      TEXT NOT NULL,
    subagent     TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    deleted_at   TEXT,
    UNIQUE (workspace_id, agent_name)
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

TABLES = ["workspaces", "workspace_agents", "topics", "sessions", "messages", "attachments"]


_MIGRATIONS = [
    "ALTER TABLE workspaces ADD COLUMN archived_at TEXT",
    "ALTER TABLE topics ADD COLUMN archived_at TEXT",
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


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists
        _migrate_workspace_name_uniqueness(conn)
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
