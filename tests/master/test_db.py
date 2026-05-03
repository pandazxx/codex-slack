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
    assert "scope_type" in info["staffs"]
    assert "is_default" in info["staffs"]
    assert "session_id" in info["staff_sessions"]
    assert "key" in info["config"]
    assert "worktree_path" in info["topics"]
    assert "llm_session_id" in info["sessions"]
    assert "transcript" in info["messages"]


def test_get_connection_enables_foreign_keys(db_path):
    conn = get_connection(db_path)
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    conn.close()
    assert row[0] == 1


def test_staffs_unique_constraint(db_path):
    conn = get_connection(db_path)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, ?)",
        ("w1", "test", "https://github.com/x/y", None, now, None),
    )
    conn.execute(
        "INSERT INTO staffs (id, scope_type, scope_id, name, adapter, model, system_prompt, agent,"
        " session_scope, is_default, extra_flags, created_at, updated_at)"
        " VALUES (?, 'workspace', ?, 'claude', 'claude-code', NULL, NULL, NULL, 'topic', 1, NULL, ?, ?)",
        ("s1", "w1", now, now),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO staffs (id, scope_type, scope_id, name, adapter, model, system_prompt, agent,"
            " session_scope, is_default, extra_flags, created_at, updated_at)"
            " VALUES (?, 'workspace', ?, 'claude', 'codex', NULL, NULL, NULL, 'topic', 0, NULL, ?, ?)",
            ("s2", "w1", now, now),
        )
        conn.commit()
    conn.close()
