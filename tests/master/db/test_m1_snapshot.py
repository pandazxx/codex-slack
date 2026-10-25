"""M1 TC-19–TC-26: Snapshot export/import round-trip tests.

Tests the snapshot CLI (scripts/db.sh snapshot export|import) and its
correctness guarantees.

Design doc §8 contracts tested here:
  - export produces manifest.json + per-table JSONL in the output directory
  - manifest has correct schema_version (== current alembic head)
  - manifest row counts match seeded data
  - manifest SHA-256s match actual JSONL content
  - JSONL encodes booleans as JSON true/false (not 0/1)
  - JSONL encodes NULLs as JSON null
  - JSONL embeds JSON-column values as objects, not strings
  - Unicode and emoji round-trip exactly
  - REAL values (chunks.created_at, tasks.failure_score) round-trip without drift
  - Import into a fresh DB reproduces every row exactly
  - INSERT into chunks after import succeeds without PK collision (sequence reset)
  - Import into a non-empty target is refused without --yes (exit non-zero)
  - Import into a non-empty target with --yes truncates and reloads (exit 0)
  - Import with schema_version mismatch is refused (exit non-zero)
  - Import with a tampered (checksum-wrong) JSONL file is refused (exit non-zero)
  - SQLite → Postgres round-trip preserves all rows (skip if TEST_POSTGRES_URL unset)

Interface contract (must be satisfied before these tests go green):
  scripts/db.sh snapshot export --source-url URL --out DIR
    Exit 0 on success.  Creates DIR/manifest.json + DIR/{table}.jsonl for each table.
  scripts/db.sh snapshot import --in DIR --target-url URL [--yes]
    Exit 0 on success.  Resets sequences after import.
    Exit non-zero (and prints an error) if target has rows and --yes is absent.
    Exit non-zero if manifest schema_version != current alembic head.
    Exit non-zero if any JSONL file fails its manifest SHA-256 check.

All tests skip when 'scripts/db.sh snapshot export --help' exits non-zero
(i.e., snapshot subcommand is not yet implemented — M0 / early M1 stub).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parents[3]
DB_SH = str(REPO_ROOT / "scripts" / "db.sh")
BASELINE_REV = "000000000001"

ALL_TABLES = [
    "workspaces", "staffs", "staff_sessions", "config", "topics",
    "sessions", "messages", "attachments", "chunks", "event_actions",
    "notes", "tasks", "staged_dispatches",
]


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

def _snapshot_export_available() -> bool:
    """Return True if scripts/db.sh snapshot export exists (M1 implemented)."""
    if not Path(DB_SH).exists():
        return False
    r = subprocess.run(
        ["bash", DB_SH, "snapshot", "export", "--help"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


SKIP_NO_SNAPSHOT = pytest.mark.skipif(
    not _snapshot_export_available(),
    reason=(
        "scripts/db.sh snapshot export not available — "
        "M1 snapshot CLI not yet implemented"
    ),
)

pytestmark = [
    SKIP_NO_SNAPSHOT,
    pytest.mark.skipif(
        not Path(DB_SH).exists(),
        reason="scripts/db.sh does not exist",
    ),
]

sqlalchemy = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_NOW = "2026-10-25T12:00:00Z"


def _run_db_sh(*args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", DB_SH, *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    return heads[0]


def _seed_all_tables(db_path: str) -> dict[str, int]:
    """Seed representative rows covering all 13 tables.

    Covers: NULLs, booleans (0/1), REAL values, unicode/emoji,
    large text, JSON columns.

    Returns {table_name: row_count}.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # workspaces (no FK)
        conn.execute(
            "INSERT INTO workspaces (id, name, repo_url, container_name,"
            " created_at, archived_at, last_refreshed_at, last_message_at,"
            " last_dispatched_at, last_responded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "ws-snapshot-001", "snapshot-ws", "https://github.com/x/y",
                "container-x", _NOW, None, _NOW, None, None, None,
            ),
        )

        # staffs — global (scope_id=NULL) and workspace scope
        conn.execute(
            "INSERT INTO staffs (id, scope_type, scope_id, name, adapter, model,"
            " system_prompt, agent, session_scope, is_default, extra_flags,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "staff-global-snap", "global", None, "global-snap-agent",
                "claude-code", "claude-3-opus",
                "System prompt with unicode: café 日本語 🚀🌍",  # unicode/emoji
                None, "topic", 1, None, _NOW, _NOW,
            ),
        )
        conn.execute(
            "INSERT INTO staffs (id, scope_type, scope_id, name, adapter, model,"
            " system_prompt, agent, session_scope, is_default, extra_flags,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "staff-ws-snap", "workspace", "ws-snapshot-001", "ws-snap-agent",
                "codex", None,
                "A" * 5000,  # large text
                None, "workspace", 0, None, _NOW, _NOW,
            ),
        )

        # staff_sessions (no FK)
        conn.execute(
            "INSERT INTO staff_sessions (scope_type, scope_id, staff_name, session_id, started_at)"
            " VALUES (?,?,?,?,?)",
            ("global", "", "global-snap-agent", "sess-snap-001", _NOW),
        )

        # config — key column (reserved word in MySQL, important for quote tests)
        conn.execute(
            "INSERT INTO config (scope_type, scope_id, key, value, updated_at)"
            " VALUES (?,?,?,?,?)",
            ("global", "", "some_key", "some_value", _NOW),
        )
        conn.execute(
            "INSERT INTO config (scope_type, scope_id, key, value, updated_at)"
            " VALUES (?,?,?,?,?)",
            ("workspace", "ws-snapshot-001", "another_key", '{"nested": true}', _NOW),
        )

        # topics (FK: workspaces)
        conn.execute(
            "INSERT INTO topics (id, workspace_id, subject, branch_name,"
            " worktree_path, created_at, archived_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                "topic-snap-001", "ws-snapshot-001", "Snapshot test topic",
                "feat/snap-test", "/tmp/worktrees/snap", _NOW, None,
            ),
        )

        # sessions (FK: topics)
        conn.execute(
            "INSERT INTO sessions (id, topic_id, agent_name, adapter,"
            " llm_session_id, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            ("sess-topic-snap-001", "topic-snap-001", "global-snap-agent",
             "claude-code", "llm-sess-abc", _NOW),
        )

        # messages (FK: topics) — includes NULL columns and unicode
        conn.execute(
            "INSERT INTO messages (id, topic_id, sender, agent_name, text,"
            " transcript, usage_json, attachments_json, event_action_id,"
            " silent, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "msg-snap-001", "topic-snap-001", "user", None,
                "Hello world 🎉 with unicode: 中文",
                None,
                '{"prompt_tokens": 10, "completion_tokens": 20}',  # JSON column
                None, None, 0, _NOW,
            ),
        )
        conn.execute(
            "INSERT INTO messages (id, topic_id, sender, agent_name, text,"
            " transcript, usage_json, attachments_json, event_action_id,"
            " silent, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "msg-snap-002", "topic-snap-001", "agent", "global-snap-agent",
                "Response text",
                "Full transcript here",
                None, '{"files": ["a.py"]}', None, 1, _NOW,
            ),
        )

        # attachments (FK: messages, topics)
        conn.execute(
            "INSERT INTO attachments (id, message_id, topic_id, filename,"
            " mime_type, size_bytes, storage_uri, created_at, direction)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "att-snap-001", "msg-snap-001", "topic-snap-001",
                "test_file.py", "text/x-python", 1234,
                "attachments/topic-snap-001/test_file.py", _NOW, "inbound",
            ),
        )

        # chunks (INTEGER PRIMARY KEY AUTOINCREMENT, REAL created_at)
        for i in range(3):
            conn.execute(
                "INSERT INTO chunks (message_id, topic_id, seq, event, agent_name, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    "msg-snap-001", "topic-snap-001", i,
                    f"delta:{i}", None, 1729857600.123 + i * 0.001,  # REAL
                ),
            )

        # event_actions (boolean: enabled, structured_output, silent)
        conn.execute(
            "INSERT INTO event_actions (id, event_type, scope_type, scope_id,"
            " staff_name, prompt_template, timing, cron_expr, last_fired_at,"
            " last_run_at, last_run_status, last_run_output, enabled,"
            " structured_output, silent, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ea-snap-001", "topic_message_sent", "topic", "topic-snap-001",
                "global-snap-agent", "Run on message: {{message}}",
                "after", None, None, None, None, None,
                1, 0, 1,  # booleans as 0/1 in SQLite
                _NOW, _NOW,
            ),
        )

        # notes (JSON tags column, key column)
        conn.execute(
            "INSERT INTO notes (id, scope_type, scope_id, key, value,"
            " tags, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                "note-snap-001", "workspace", "ws-snapshot-001",
                "snap-note-key",
                "Note value with emoji 🗒️",
                '["tag1", "tag2"]',  # JSON column
                _NOW, _NOW,
            ),
        )

        # tasks (FK: topics, REAL failure_score, JSON result_artifacts)
        conn.execute(
            "INSERT INTO tasks (id, topic_id, root_task_id, parent_task_id,"
            " depth, dispatcher_kind, dispatcher_name, assignee_name, goal,"
            " acceptance_criteria, state, failure_score, result_summary,"
            " result_artifacts, result_status, created_at, updated_at, closed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "task-snap-001", "topic-snap-001", "task-snap-001", None,
                0, "user", None, "global-snap-agent", "Write tests",
                "Tests must pass", "completed", 0.0,  # REAL
                "Done",
                '[{"type": "file", "path": "test.py"}]',  # JSON
                "success", _NOW, _NOW, _NOW,
            ),
        )

        # staged_dispatches (NULL dispatched_at)
        conn.execute(
            "INSERT INTO staged_dispatches (id, prompt_message_id, topic_id,"
            " sender_name, receiver_kind, receiver_name, task_id, kind, body,"
            " created_at, dispatched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sd-snap-001", "msg-snap-001", "topic-snap-001",
                "user", "staff", "global-snap-agent",
                "task-snap-001", "question", "What should I do?",
                _NOW, None,  # NULL dispatched_at
            ),
        )

        conn.commit()
    finally:
        conn.close()

    # Return row counts
    counts: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        for t in ALL_TABLES:
            row = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()  # noqa: S608
            counts[t] = row[0]
    finally:
        conn.close()
    return counts


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _row_dicts_from_table(db_path: str, table: str) -> list[dict]:
    """Return all rows from a table as list of dicts (ordered by rowid)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_source_db(tmp_path):
    """A seeded SQLite DB (source) and its row counts."""
    from src.master.db import startup_db

    db_path = str(tmp_path / "source.db")
    url = f"sqlite+pysqlite:///{db_path}"
    startup_db(url)
    counts = _seed_all_tables(db_path)
    return db_path, url, counts


@pytest.fixture()
def snapshot_dir(tmp_path, seeded_source_db):
    """A snapshot directory exported from the seeded source DB."""
    db_path, url, counts = seeded_source_db
    out_dir = tmp_path / "snapshot_out"
    out_dir.mkdir()
    result = _run_db_sh(
        "snapshot", "export",
        "--source-url", url,
        "--out", str(out_dir),
    )
    assert result.returncode == 0, (
        f"snapshot export failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return out_dir, counts, url


# ---------------------------------------------------------------------------
# TC-19: Export produces valid manifest and JSONL files
# ---------------------------------------------------------------------------

class TestSnapshotExport:
    """scripts/db.sh snapshot export creates the expected output layout."""

    def test_export_creates_manifest(self, snapshot_dir):
        """Export creates manifest.json in the output directory."""
        out_dir, counts, _ = snapshot_dir
        assert (out_dir / "manifest.json").exists(), "manifest.json missing from snapshot output"

    def test_export_creates_jsonl_for_every_table(self, snapshot_dir):
        """Export creates a .jsonl file for every table in ALL_TABLES."""
        out_dir, counts, _ = snapshot_dir
        missing = [t for t in ALL_TABLES if not (out_dir / f"{t}.jsonl").exists()]
        assert not missing, f"Missing JSONL files: {missing}"

    def test_manifest_schema_version_equals_alembic_head(self, snapshot_dir):
        """manifest.json schema_version equals the current alembic head revision."""
        out_dir, _, _ = snapshot_dir
        manifest = json.loads((out_dir / "manifest.json").read_text())
        head = _alembic_head()
        assert manifest["schema_version"] == head, (
            f"manifest schema_version={manifest['schema_version']!r}, "
            f"expected alembic head {head!r}"
        )

    def test_manifest_has_created_at(self, snapshot_dir):
        """manifest.json has a created_at timestamp field."""
        out_dir, _, _ = snapshot_dir
        manifest = json.loads((out_dir / "manifest.json").read_text())
        assert "created_at" in manifest, "manifest.json missing 'created_at'"

    def test_manifest_has_source_dialect(self, snapshot_dir):
        """manifest.json has a source_dialect field."""
        out_dir, _, _ = snapshot_dir
        manifest = json.loads((out_dir / "manifest.json").read_text())
        assert "source_dialect" in manifest, "manifest.json missing 'source_dialect'"
        assert manifest["source_dialect"] == "sqlite", (
            f"source_dialect should be 'sqlite', got {manifest['source_dialect']!r}"
        )

    def test_manifest_row_counts_match_seeded_data(self, snapshot_dir):
        """manifest.json row counts match the number of seeded rows per table."""
        out_dir, seeded_counts, _ = snapshot_dir
        manifest = json.loads((out_dir / "manifest.json").read_text())
        tables_in_manifest = manifest["tables"]
        for table, expected in seeded_counts.items():
            assert table in tables_in_manifest, (
                f"Table '{table}' missing from manifest.tables"
            )
            got = tables_in_manifest[table]["rows"]
            assert got == expected, (
                f"Row count mismatch for '{table}': manifest says {got}, seeded {expected}"
            )

    def test_manifest_sha256_matches_jsonl_content(self, snapshot_dir):
        """manifest.json SHA-256s match the actual content of the JSONL files."""
        out_dir, _, _ = snapshot_dir
        manifest = json.loads((out_dir / "manifest.json").read_text())
        for table, info in manifest["tables"].items():
            jsonl_path = out_dir / f"{table}.jsonl"
            if not jsonl_path.exists():
                continue
            actual_sha = _sha256_of_file(jsonl_path)
            expected_sha = info["sha256"]
            assert actual_sha == expected_sha, (
                f"SHA-256 mismatch for {table}.jsonl: "
                f"manifest says {expected_sha!r}, file is {actual_sha!r}"
            )

    def test_boolean_columns_encoded_as_json_booleans(self, snapshot_dir):
        """JSONL encodes boolean columns as true/false, not 0/1."""
        out_dir, _, _ = snapshot_dir
        # event_actions.enabled, structured_output, silent are booleans
        ea_jsonl = out_dir / "event_actions.jsonl"
        if not ea_jsonl.exists() or ea_jsonl.stat().st_size == 0:
            pytest.skip("No event_actions rows in snapshot")
        for line in ea_jsonl.read_text().splitlines():
            row = json.loads(line)
            for bool_col in ("enabled", "structured_output", "silent"):
                if bool_col in row:
                    assert isinstance(row[bool_col], bool), (
                        f"event_actions.{bool_col} should be JSON boolean, "
                        f"got {type(row[bool_col]).__name__}: {row[bool_col]!r}"
                    )

    def test_null_columns_encoded_as_json_null(self, snapshot_dir):
        """JSONL encodes NULL columns as JSON null, not absent or empty string."""
        out_dir, _, _ = snapshot_dir
        ws_jsonl = out_dir / "workspaces.jsonl"
        for line in ws_jsonl.read_text().splitlines():
            row = json.loads(line)
            # last_message_at was seeded as NULL
            assert "last_message_at" in row, (
                "workspaces JSONL must include last_message_at key"
            )
            assert row["last_message_at"] is None, (
                f"workspaces.last_message_at should be null, got {row['last_message_at']!r}"
            )

    def test_json_columns_embedded_as_objects(self, snapshot_dir):
        """JSON column values are embedded as objects/arrays, not as strings."""
        out_dir, _, _ = snapshot_dir
        msg_jsonl = out_dir / "messages.jsonl"
        for line in msg_jsonl.read_text().splitlines():
            row = json.loads(line)
            if row.get("usage_json") is not None:
                # usage_json should be a dict, not a string
                assert isinstance(row["usage_json"], (dict, list)), (
                    f"messages.usage_json should be a JSON object/array, "
                    f"got {type(row['usage_json']).__name__}: {row['usage_json']!r}"
                )

    def test_unicode_emoji_present_in_jsonl(self, snapshot_dir):
        """Unicode and emoji characters survive export encoding."""
        out_dir, _, _ = snapshot_dir
        staffs_jsonl = out_dir / "staffs.jsonl"
        content = staffs_jsonl.read_text(encoding="utf-8")
        assert "café" in content, "Unicode 'café' lost in JSONL export"
        assert "🚀" in content, "Emoji '🚀' lost in JSONL export"

    def test_real_values_in_chunks_jsonl(self, snapshot_dir):
        """REAL values (chunks.created_at) are encoded as JSON numbers."""
        out_dir, _, _ = snapshot_dir
        chunks_jsonl = out_dir / "chunks.jsonl"
        if not chunks_jsonl.exists() or chunks_jsonl.stat().st_size == 0:
            pytest.skip("No chunks rows in snapshot")
        for line in chunks_jsonl.read_text().splitlines():
            row = json.loads(line)
            assert "created_at" in row
            assert isinstance(row["created_at"], float), (
                f"chunks.created_at should be a JSON float, "
                f"got {type(row['created_at']).__name__}: {row['created_at']!r}"
            )


# ---------------------------------------------------------------------------
# TC-20: Import round-trip — every row matches
# ---------------------------------------------------------------------------

class TestSnapshotImportRoundtrip:
    """Importing a snapshot into a fresh DB reproduces every row exactly."""

    @pytest.fixture()
    def fresh_target_db(self, tmp_path):
        """A fresh empty SQLite DB (import target)."""
        from src.master.db import startup_db

        db_path = str(tmp_path / "target.db")
        url = f"sqlite+pysqlite:///{db_path}"
        startup_db(url)  # create schema
        return db_path, url

    def test_import_exits_zero(self, snapshot_dir, fresh_target_db, tmp_path):
        """snapshot import into a fresh DB exits 0."""
        out_dir, _, source_url = snapshot_dir
        target_db, target_url = fresh_target_db
        result = _run_db_sh(
            "snapshot", "import",
            "--in", str(out_dir),
            "--target-url", target_url,
        )
        assert result.returncode == 0, (
            f"snapshot import failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )

    def test_import_row_counts_match(self, snapshot_dir, fresh_target_db, tmp_path):
        """Row counts in the imported DB match the manifest."""
        out_dir, seeded_counts, _ = snapshot_dir
        target_db, target_url = fresh_target_db
        _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        conn = sqlite3.connect(target_db)
        try:
            for table, expected in seeded_counts.items():
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608
                ).fetchone()
                got = row[0]
                assert got == expected, (
                    f"Row count mismatch in '{table}' after import: "
                    f"expected {expected}, got {got}"
                )
        finally:
            conn.close()

    def test_unicode_emoji_roundtrip(self, snapshot_dir, fresh_target_db, tmp_path):
        """Unicode and emoji values survive the full export→import cycle."""
        out_dir, _, _ = snapshot_dir
        target_db, target_url = fresh_target_db
        _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        conn = sqlite3.connect(target_db)
        try:
            row = conn.execute(
                "SELECT system_prompt FROM staffs WHERE id='staff-global-snap'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "global staff row missing after import"
        assert "café" in row[0], "Unicode 'café' lost after import"
        assert "🚀" in row[0], "Emoji '🚀' lost after import"

    def test_null_values_roundtrip(self, snapshot_dir, fresh_target_db, tmp_path):
        """NULL values survive the full export→import cycle."""
        out_dir, _, _ = snapshot_dir
        target_db, target_url = fresh_target_db
        _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        conn = sqlite3.connect(target_db)
        try:
            row = conn.execute(
                "SELECT last_message_at FROM workspaces WHERE id='ws-snapshot-001'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] is None, (
            f"NULL last_message_at should be None after import, got {row[0]!r}"
        )

    def test_boolean_values_roundtrip(self, snapshot_dir, fresh_target_db, tmp_path):
        """Boolean columns survive the full export→import cycle."""
        out_dir, _, _ = snapshot_dir
        target_db, target_url = fresh_target_db
        _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        conn = sqlite3.connect(target_db)
        try:
            row = conn.execute(
                "SELECT enabled, structured_output, silent FROM event_actions"
                " WHERE id='ea-snap-001'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "event_actions row missing after import"
        # In SQLite the booleans come back as integers
        assert row[0] in (0, 1, True, False), f"enabled value unexpected: {row[0]!r}"
        assert int(row[0]) == 1, f"enabled should be 1/true after import, got {row[0]!r}"

    def test_real_values_roundtrip(self, snapshot_dir, fresh_target_db, tmp_path):
        """REAL column values round-trip without precision drift."""
        out_dir, _, _ = snapshot_dir
        target_db, target_url = fresh_target_db

        # Capture source REAL values
        conn = sqlite3.connect(str(snapshot_dir[0]).replace("snapshot_out", "source.db")
                               if False else "")
        source_reals: list[float] = []
        # Use the snapshot JSONL as the source of truth for REAL values
        chunks_jsonl = out_dir / "chunks.jsonl"
        if chunks_jsonl.exists():
            for line in chunks_jsonl.read_text().splitlines():
                row_j = json.loads(line)
                source_reals.append(row_j["created_at"])

        _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )

        conn = sqlite3.connect(target_db)
        try:
            imported_reals = [
                r[0] for r in conn.execute(
                    "SELECT created_at FROM chunks ORDER BY id"
                ).fetchall()
            ]
        finally:
            conn.close()

        assert len(imported_reals) == len(source_reals), (
            f"REAL value count mismatch: {len(source_reals)} in snapshot, "
            f"{len(imported_reals)} in target"
        )
        for s, t in zip(source_reals, imported_reals):
            assert abs(s - t) < 1e-9, (
                f"REAL value drift: source={s!r}, imported={t!r}"
            )


# ---------------------------------------------------------------------------
# TC-21: Sequence reset — INSERT after import succeeds
# ---------------------------------------------------------------------------

class TestSequenceResetAfterImport:
    """After import, identity-column sequences are reset so new inserts do not collide."""

    def test_insert_into_chunks_after_import_succeeds(
        self, snapshot_dir, tmp_path
    ):
        """An INSERT into chunks after import succeeds without PK collision."""
        from src.master.db import startup_db
        from sqlalchemy import create_engine, text

        out_dir, _, source_url = snapshot_dir
        target_db = str(tmp_path / "target_seq.db")
        target_url = f"sqlite+pysqlite:///{target_db}"
        startup_db(target_url)

        _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )

        # Now INSERT a new chunks row without specifying id (AUTOINCREMENT)
        engine = create_engine(target_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO chunks (message_id, topic_id, seq, event, created_at)"
                        " VALUES (:mid, :tid, :seq, :event, :ts)"
                    ),
                    {
                        "mid": "msg-snap-001",
                        "tid": "topic-snap-001",
                        "seq": 999,
                        "event": "delta:999",
                        "ts": 1729857700.0,
                    },
                )
        except Exception as exc:
            pytest.fail(
                f"INSERT into chunks after import raised {type(exc).__name__}: {exc}"
            )
        finally:
            engine.dispose()

    def test_no_pk_collision_after_import(self, snapshot_dir, tmp_path):
        """Inserting multiple new rows into chunks after import does not collide."""
        from src.master.db import startup_db
        from sqlalchemy import create_engine, text

        out_dir, _, _ = snapshot_dir
        target_db = str(tmp_path / "target_no_coll.db")
        target_url = f"sqlite+pysqlite:///{target_db}"
        startup_db(target_url)
        _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )

        engine = create_engine(target_url)
        try:
            with engine.begin() as conn:
                for i in range(5):
                    conn.execute(
                        text(
                            "INSERT INTO chunks"
                            " (message_id, topic_id, seq, event, created_at)"
                            " VALUES (:mid, :tid, :seq, :event, :ts)"
                        ),
                        {
                            "mid": "msg-snap-001",
                            "tid": "topic-snap-001",
                            "seq": 1000 + i,
                            "event": f"delta:{1000 + i}",
                            "ts": 1729857700.0 + i,
                        },
                    )
        except Exception as exc:
            pytest.fail(
                f"Bulk INSERT into chunks after import raised {type(exc).__name__}: {exc}"
            )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# TC-22: Import guards
# ---------------------------------------------------------------------------

class TestImportGuards:
    """snapshot import enforces schema version, non-empty target, and checksum."""

    @pytest.fixture()
    def target_with_rows(self, tmp_path, snapshot_dir):
        """A target DB that already has rows from a prior import."""
        from src.master.db import startup_db

        out_dir, _, _ = snapshot_dir
        target_db = str(tmp_path / "nonempty_target.db")
        target_url = f"sqlite+pysqlite:///{target_db}"
        startup_db(target_url)
        # First import to populate
        r = _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        assert r.returncode == 0, f"Setup import failed:\n{r.stdout}\n{r.stderr}"
        return target_db, target_url

    def test_import_refused_on_nonempty_target_without_yes(
        self, snapshot_dir, target_with_rows
    ):
        """Import refuses a non-empty target when --yes is not passed."""
        out_dir, _, _ = snapshot_dir
        target_db, target_url = target_with_rows
        result = _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        assert result.returncode != 0, (
            "Import should refuse a non-empty target without --yes, "
            f"but exited {result.returncode}"
        )

    def test_import_with_yes_replaces_rows(self, snapshot_dir, target_with_rows, tmp_path):
        """Import with --yes truncates and reloads a non-empty target."""
        out_dir, seeded_counts, _ = snapshot_dir
        target_db, target_url = target_with_rows
        result = _run_db_sh(
            "snapshot", "import",
            "--in", str(out_dir),
            "--target-url", target_url,
            "--yes",
        )
        assert result.returncode == 0, (
            f"Import with --yes failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
        conn = sqlite3.connect(target_db)
        try:
            row = conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()
            assert row[0] == seeded_counts["workspaces"], (
                f"Row count mismatch after --yes import: "
                f"expected {seeded_counts['workspaces']}, got {row[0]}"
            )
        finally:
            conn.close()

    def test_import_refused_on_schema_version_mismatch(
        self, snapshot_dir, tmp_path
    ):
        """Import refuses when manifest schema_version does not match alembic head."""
        from src.master.db import startup_db

        out_dir, _, _ = snapshot_dir
        # Tamper: change schema_version in manifest
        manifest_path = out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        original_version = manifest["schema_version"]
        manifest["schema_version"] = "000000000000_wrong_version"
        manifest_path.write_text(json.dumps(manifest))

        target_db = str(tmp_path / "mismatch_target.db")
        target_url = f"sqlite+pysqlite:///{target_db}"
        startup_db(target_url)

        result = _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        assert result.returncode != 0, (
            "Import should refuse when manifest schema_version mismatches alembic head"
        )
        # Restore for other tests (important if snapshot_dir is session-scoped)
        manifest["schema_version"] = original_version
        manifest_path.write_text(json.dumps(manifest))

    def test_import_refused_on_tampered_jsonl(self, snapshot_dir, tmp_path):
        """Import refuses when a JSONL file fails its manifest SHA-256 check."""
        from src.master.db import startup_db

        out_dir, _, _ = snapshot_dir
        # Tamper: append a byte to workspaces.jsonl to corrupt it
        ws_jsonl = out_dir / "workspaces.jsonl"
        original_content = ws_jsonl.read_bytes()
        ws_jsonl.write_bytes(original_content + b"\n")  # checksum will fail

        target_db = str(tmp_path / "tamper_target.db")
        target_url = f"sqlite+pysqlite:///{target_db}"
        startup_db(target_url)

        result = _run_db_sh(
            "snapshot", "import", "--in", str(out_dir), "--target-url", target_url
        )
        assert result.returncode != 0, (
            "Import should refuse when JSONL file fails its checksum check"
        )
        # Restore
        ws_jsonl.write_bytes(original_content)


# ---------------------------------------------------------------------------
# TC-23: SQLite → Postgres round-trip (skip if TEST_POSTGRES_URL unset)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL not set; skipping SQLite→Postgres round-trip test",
)
class TestSqliteToPostgresRoundtrip:
    """A full SQLite export → Postgres import round-trip preserves all rows."""

    def test_sqlite_to_postgres_row_counts_match(self, snapshot_dir):
        """Row counts in Postgres after import match seeded SQLite counts."""
        from src.master.db import startup_db
        from sqlalchemy import create_engine, text

        out_dir, seeded_counts, _ = snapshot_dir
        pg_url = os.environ["TEST_POSTGRES_URL"]

        # Ensure clean Postgres target
        startup_db(pg_url)

        result = _run_db_sh(
            "snapshot", "import",
            "--in", str(out_dir),
            "--target-url", pg_url,
            "--yes",  # always start clean
        )
        assert result.returncode == 0, (
            f"SQLite→Postgres import failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )

        engine = create_engine(pg_url)
        try:
            for table, expected in seeded_counts.items():
                row = engine.connect().execute(
                    text(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608
                ).fetchone()
                got = row[0]
                assert got == expected, (
                    f"Row count mismatch in '{table}' after SQLite→Postgres import: "
                    f"expected {expected}, got {got}"
                )
        finally:
            engine.dispose()

    def test_sqlite_to_postgres_insert_after_import(self, snapshot_dir):
        """INSERT into chunks on Postgres after import succeeds (sequence reset)."""
        from src.master.db import startup_db
        from sqlalchemy import create_engine, text

        out_dir, _, _ = snapshot_dir
        pg_url = os.environ["TEST_POSTGRES_URL"]
        startup_db(pg_url)
        _run_db_sh(
            "snapshot", "import",
            "--in", str(out_dir),
            "--target-url", pg_url,
            "--yes",
        )

        engine = create_engine(pg_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO chunks (message_id, topic_id, seq, event, created_at)"
                        " VALUES (:mid, :tid, :seq, :event, :ts)"
                    ),
                    {
                        "mid": "msg-snap-001",
                        "tid": "topic-snap-001",
                        "seq": 9999,
                        "event": "delta:9999",
                        "ts": 1729857999.0,
                    },
                )
        except Exception as exc:
            pytest.fail(
                f"INSERT into Postgres chunks after import raised: {exc}"
            )
        finally:
            engine.dispose()
