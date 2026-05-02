from __future__ import annotations

import sqlite3

import pytest

from src.master.db import TABLES, get_connection, init_db, schema_info


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def test_init_db_creates_file(tmp_path):
    path = str(tmp_path / "sub" / "master_data.db")
    init_db(path)
    import os
    assert os.path.exists(path)


def test_init_db_creates_all_tables(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    names = {r[0] for r in rows}
    assert set(TABLES) <= names


def test_init_db_is_idempotent(db_path):
    init_db(db_path)
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    assert len([r for r in rows if r[0] in TABLES]) == len(TABLES)


def test_schema_info_returns_columns(db_path):
    info = schema_info(db_path)
    assert set(info.keys()) == set(TABLES)
    assert "id" in info["workspaces"]
    assert "repo_url" in info["workspaces"]
    assert "active" in info["workspace_agents"]
    assert "deleted_at" in info["workspace_agents"]
    assert "worktree_path" in info["topics"]
    assert "llm_session_id" in info["sessions"]
    assert "transcript" in info["messages"]


def test_get_connection_enables_foreign_keys(db_path):
    conn = get_connection(db_path)
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    conn.close()
    assert row[0] == 1


def test_workspace_agents_unique_constraint(db_path):
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?)",
        ("w1", "test", "https://github.com/x/y", None, "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO workspace_agents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("a1", "w1", "claude", "claude-code", None, 1, "2026-01-01T00:00:00Z", None),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO workspace_agents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("a2", "w1", "claude", "codex", None, 1, "2026-01-01T00:00:00Z", None),
        )
        conn.commit()
    conn.close()
