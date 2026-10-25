"""Snapshot export and import for the master database.

Implements the provider-neutral JSONL snapshot format described in design §8.

Export writes manifest.json + one {table}.jsonl per table.
Import reads them back into any supported target dialect, resets sequences,
and verifies row counts.

Type encoding on export (§8 type-normalisation table):
  - TEXT columns: string unchanged
  - INTEGER columns: number (JSON int)
  - REAL / Float columns (chunks.created_at, tasks.failure_score): JSON float
  - Logically-boolean INTEGER columns (is_default, enabled, etc.): JSON bool
  - JSON-text columns (usage_json, attachments_json, tags, result_artifacts): embedded object
  - NULL: JSON null
  - BLOB (not present today): base64 string
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect as sa_inspect, text as sa_text
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Table ordering: parents before children (FK dependency order)
# ---------------------------------------------------------------------------

EXPORT_ORDER = [
    "workspaces",
    "staffs",
    "staff_sessions",
    "config",
    "topics",
    "sessions",
    "messages",
    "attachments",
    "chunks",
    "event_actions",
    "notes",
    "tasks",
    "staged_dispatches",
]

# Reverse order for truncation on --yes import
TRUNCATE_ORDER = list(reversed(EXPORT_ORDER))

# ---------------------------------------------------------------------------
# Column type metadata (columns that need special encoding/decoding)
# ---------------------------------------------------------------------------

# Columns stored as INTEGER in SQLite but logically boolean (0/1)
BOOLEAN_COLUMNS: dict[str, set[str]] = {
    "staffs": {"is_default"},
    "messages": {"silent"},
    "event_actions": {"enabled", "structured_output", "silent"},
}

# Columns stored as TEXT that contain embedded JSON objects/arrays
JSON_TEXT_COLUMNS: dict[str, set[str]] = {
    "messages": {"usage_json", "attachments_json"},
    "notes": {"tags"},
    "tasks": {"result_artifacts"},
}

# Float columns (REAL)
FLOAT_COLUMNS: dict[str, set[str]] = {
    "chunks": {"created_at"},
    "tasks": {"failure_score"},
}

# Tables that have an autoincrement identity column needing sequence reset
AUTOINCREMENT_TABLES: dict[str, str] = {
    "chunks": "id",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BATCH_SIZE = 500


def _make_engine(url: str) -> Engine:
    from . import _make_engine as _db_make_engine
    return _db_make_engine(url)


def _alembic_head(repo_root: Path) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected exactly 1 alembic head, got {heads}")
    return heads[0]


def _get_current_revision(engine: Engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def _dialect_name(engine: Engine) -> str:
    return engine.dialect.name  # 'sqlite', 'postgresql', 'mysql'


def _encode_value(
    table: str,
    col: str,
    value: Any,
    bool_cols: set[str],
    json_cols: set[str],
    float_cols: set[str],
) -> Any:
    """Encode a single column value for JSON export."""
    if value is None:
        return None
    if col in bool_cols:
        return bool(value)
    if col in float_cols:
        return float(value)
    if col in json_cols:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def _decode_value(
    table: str,
    col: str,
    value: Any,
    bool_cols: set[str],
    json_cols: set[str],
    float_cols: set[str],
    dialect: str,
) -> Any:
    """Decode a single column value from JSON for database insert."""
    if value is None:
        return None
    if col in bool_cols:
        return bool(value)
    if col in float_cols:
        return float(value)
    if col in json_cols:
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value
    if isinstance(value, str) and len(value) > 0:
        return value
    return value


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_snapshot(source_url: str, out_dir: Path, repo_root: Path) -> None:
    """Export the source database to a snapshot directory.

    Creates out_dir/manifest.json and out_dir/{table}.jsonl for each table.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    head = _alembic_head(repo_root)
    engine = _make_engine(source_url)
    dialect = _dialect_name(engine)

    try:
        insp = sa_inspect(engine)
        existing_tables = set(insp.get_table_names())

        tables_meta: dict[str, dict] = {}

        for table in EXPORT_ORDER:
            if table not in existing_tables:
                print(f"  warning: table '{table}' not found in source — skipping", file=sys.stderr)
                continue

            jsonl_path = out_dir / f"{table}.jsonl"
            bool_cols = BOOLEAN_COLUMNS.get(table, set())
            json_cols = JSON_TEXT_COLUMNS.get(table, set())
            float_cols = FLOAT_COLUMNS.get(table, set())

            row_count = 0
            with engine.connect() as conn:
                result = conn.execute(sa_text(f"SELECT * FROM \"{table}\""))  # noqa: S608
                col_names = list(result.keys())

                with jsonl_path.open("w", encoding="utf-8") as fh:
                    while True:
                        batch = result.fetchmany(_BATCH_SIZE)
                        if not batch:
                            break
                        for row in batch:
                            encoded = {
                                col: _encode_value(
                                    table, col, row[i],
                                    bool_cols, json_cols, float_cols,
                                )
                                for i, col in enumerate(col_names)
                            }
                            fh.write(json.dumps(encoded, ensure_ascii=False) + "\n")
                            row_count += 1

            sha256 = _sha256_file(jsonl_path)
            tables_meta[table] = {"rows": row_count, "sha256": sha256}
            print(f"  exported {table}: {row_count} rows")

    finally:
        engine.dispose()

    manifest = {
        "schema_version": head,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_dialect": dialect,
        "tables": tables_meta,
        "tool_version": "M1",
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"Snapshot written to {out_dir}")


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

_DEFAULT_LOCK_TIMEOUT = "30s"

_LOCK_TIMEOUT_MSG = (
    "ERROR: target database is in use (lock timeout after {timeout}) — "
    "stop master before importing; "
    "see docs/guides/runbooks/migrate-sqlite-to-postgres.md"
)


def _acquire_truncate_locks(engine: Engine, dialect: str, timeout: str) -> None:
    """Set a lock timeout, then pre-lock every application table with ACCESS EXCLUSIVE.

    If any lock cannot be acquired within *timeout* the function prints an
    actionable error message and calls sys.exit(1) rather than hanging.
    """
    if dialect not in ("postgresql", "mysql"):
        return

    from sqlalchemy.exc import OperationalError

    try:
        with engine.begin() as conn:
            if dialect == "postgresql":
                conn.execute(sa_text(f"SET LOCAL lock_timeout = '{timeout}'"))
                for table in TRUNCATE_ORDER:
                    conn.execute(
                        sa_text(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE')  # noqa: S608
                    )
            elif dialect == "mysql":
                # Convert Postgres-style timeout string (e.g. "30s") to integer seconds.
                secs = _parse_timeout_to_seconds(timeout)
                conn.execute(sa_text(f"SET SESSION innodb_lock_wait_timeout = {secs}"))
    except OperationalError as exc:
        msg = str(exc).lower()
        if "lock" in msg and ("timeout" in msg or "wait" in msg):
            print(_LOCK_TIMEOUT_MSG.format(timeout=timeout), file=sys.stderr)
            sys.exit(1)
        raise


def _parse_timeout_to_seconds(timeout: str) -> int:
    """Convert a timeout string like '30s' or '2min' to integer seconds."""
    timeout = timeout.strip()
    if timeout.endswith("ms"):
        return max(1, int(timeout[:-2]) // 1000)
    if timeout.endswith("min"):
        return int(timeout[:-3]) * 60
    if timeout.endswith("s"):
        return int(timeout[:-1])
    return int(timeout)


def import_snapshot(
    in_dir: Path,
    target_url: str,
    repo_root: Path,
    *,
    yes: bool = False,
    lock_timeout: str = _DEFAULT_LOCK_TIMEOUT,
) -> None:
    """Import a snapshot directory into the target database.

    Checks SHA-256 integrity and schema version before modifying anything.
    Refuses non-empty target unless --yes is given.

    On Postgres the TRUNCATE phase sets lock_timeout so that a concurrent
    reader (e.g. a running master process) causes a fast failure rather than
    an indefinite hang.  On MySQL the session-level lock_wait_timeout is set
    for the same reason.  Pass lock_timeout='0' to disable (not recommended).
    """
    manifest_path = in_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: manifest.json not found in {in_dir}", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text())

    # --- SHA-256 integrity check ---
    print("Checking SHA-256 integrity...")
    for table, info in manifest["tables"].items():
        jsonl = in_dir / f"{table}.jsonl"
        if not jsonl.exists():
            print(f"ERROR: {table}.jsonl missing from snapshot directory", file=sys.stderr)
            sys.exit(1)
        actual = _sha256_file(jsonl)
        if actual != info["sha256"]:
            print(
                f"ERROR: SHA-256 mismatch for {table}.jsonl\n"
                f"  expected: {info['sha256']}\n"
                f"  actual:   {actual}",
                file=sys.stderr,
            )
            sys.exit(1)
    print("  all checksums OK")

    # --- Schema version check ---
    head = _alembic_head(repo_root)
    snap_version = manifest["schema_version"]
    if snap_version != head:
        print(
            f"ERROR: snapshot schema_version {snap_version!r} does not match "
            f"current alembic head {head!r}.\n"
            f"Upgrade the source database first, take a new snapshot, then import.",
            file=sys.stderr,
        )
        sys.exit(1)

    engine = _make_engine(target_url)
    dialect = _dialect_name(engine)

    try:
        # Bring target schema to head (auto-upgrade on empty DB, or verify)
        from . import startup_db
        startup_db(target_url)

        # Re-create engine after startup_db (it creates its own)
        engine.dispose()
        engine = _make_engine(target_url)

        # --- Non-empty target check ---
        with engine.connect() as conn:
            non_empty_tables = []
            for table in EXPORT_ORDER:
                if table not in manifest["tables"]:
                    continue
                try:
                    row = conn.execute(
                        sa_text(f"SELECT COUNT(*) FROM \"{table}\"")  # noqa: S608
                    ).fetchone()
                    if row and row[0] > 0:
                        non_empty_tables.append(table)
                except Exception:
                    pass

        if non_empty_tables and not yes:
            print(
                f"ERROR: target database already has rows in: {non_empty_tables}\n"
                f"Pass --yes to truncate and reload.",
                file=sys.stderr,
            )
            sys.exit(1)

        if non_empty_tables and yes:
            print("--yes given: truncating all tables before reload...")
            _acquire_truncate_locks(engine, dialect, lock_timeout)
            with engine.begin() as conn:
                if dialect == "postgresql":
                    conn.execute(sa_text("SET session_replication_role = replica"))
                elif dialect == "sqlite":
                    conn.execute(sa_text("PRAGMA foreign_keys = OFF"))
                for table in TRUNCATE_ORDER:
                    if table not in manifest["tables"]:
                        continue
                    conn.execute(sa_text(f"DELETE FROM \"{table}\""))  # noqa: S608
                if dialect == "postgresql":
                    conn.execute(sa_text("SET session_replication_role = DEFAULT"))
                elif dialect == "sqlite":
                    conn.execute(sa_text("PRAGMA foreign_keys = ON"))

        # --- Load rows ---
        print("Loading rows...")
        for table in EXPORT_ORDER:
            if table not in manifest["tables"]:
                continue
            jsonl = in_dir / f"{table}.jsonl"
            bool_cols = BOOLEAN_COLUMNS.get(table, set())
            json_cols = JSON_TEXT_COLUMNS.get(table, set())
            float_cols = FLOAT_COLUMNS.get(table, set())

            lines = [l for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
            if not lines:
                print(f"  {table}: 0 rows (empty JSONL)")
                continue

            first = json.loads(lines[0])
            col_names = list(first.keys())
            placeholders = ", ".join(f":{c}" for c in col_names)
            quoted_cols = ", ".join(f'"{c}"' for c in col_names)
            insert_sql = sa_text(
                f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'  # noqa: S608
            )

            batch: list[dict] = []
            inserted = 0
            with engine.begin() as conn:
                for line in lines:
                    row = json.loads(line)
                    decoded = {
                        col: _decode_value(
                            table, col, row.get(col),
                            bool_cols, json_cols, float_cols, dialect,
                        )
                        for col in col_names
                    }
                    batch.append(decoded)
                    if len(batch) >= _BATCH_SIZE:
                        conn.execute(insert_sql, batch)
                        inserted += len(batch)
                        batch = []
                if batch:
                    conn.execute(insert_sql, batch)
                    inserted += len(batch)

            print(f"  {table}: {inserted} rows inserted")

        # --- Reset sequences ---
        print("Resetting sequences...")
        with engine.begin() as conn:
            for table, col in AUTOINCREMENT_TABLES.items():
                if table not in manifest["tables"]:
                    continue
                _reset_sequence(conn, table, col, dialect)

        # --- Verify row counts ---
        print("Verifying row counts...")
        with engine.connect() as conn:
            for table, info in manifest["tables"].items():
                row = conn.execute(
                    sa_text(f"SELECT COUNT(*) FROM \"{table}\"")  # noqa: S608
                ).fetchone()
                got = row[0]
                expected = info["rows"]
                if got != expected:
                    print(
                        f"ERROR: row count mismatch for '{table}': "
                        f"expected {expected}, got {got}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
        print("Import complete — all row counts verified.")

    finally:
        engine.dispose()


def _reset_sequence(conn: Any, table: str, col: str, dialect: str) -> None:
    """Reset the identity sequence for a table/column after bulk import."""
    max_row = conn.execute(
        sa_text(f'SELECT MAX("{col}") FROM "{table}"')  # noqa: S608
    ).fetchone()
    max_val = max_row[0] if max_row and max_row[0] is not None else 0

    if dialect == "postgresql":
        conn.execute(
            sa_text(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                f":max_val, true)"
            ),
            {"max_val": max(max_val, 1)},
        )
    elif dialect == "mysql":
        conn.execute(
            sa_text(f"ALTER TABLE `{table}` AUTO_INCREMENT = :next_val"),
            {"next_val": max_val + 1},
        )
    elif dialect == "sqlite":
        conn.execute(
            sa_text(
                "UPDATE sqlite_sequence SET seq = :max_val WHERE name = :table"
            ),
            {"max_val": max_val, "table": table},
        )
    print(f"  reset sequence for {table}.{col} → {max_val}")


# ---------------------------------------------------------------------------
# snapshot-and-migrate
# ---------------------------------------------------------------------------

def snapshot_and_migrate(
    source_url: str,
    target_url: str | None,
    out_dir: Path,
    repo_root: Path,
) -> None:
    """Take a snapshot of the source, then migrate or import to the target.

    Two modes (§8 / §11):
      Same URL (or target_url is None): snapshot in-place, then run
        `alembic upgrade head` on the source (schema-behind case).
      Different URL: snapshot the source, then import into the target
        (provider-migration case, e.g. SQLite → Postgres).
    """
    print(f"Snapshotting {source_url} → {out_dir}")
    export_snapshot(source_url, out_dir, repo_root)

    same_target = (target_url is None) or (target_url == source_url)

    if same_target:
        print(f"Target is same as source — running alembic upgrade head on {source_url}")
        from alembic.config import Config
        from alembic import command as alembic_command

        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", source_url)
        old = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = source_url
        try:
            alembic_command.upgrade(cfg, "head")
        finally:
            if old is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old
        print("Migration complete.")
    else:
        print(f"Importing snapshot into {target_url}")
        import_snapshot(out_dir, target_url, repo_root, yes=False)
        print("Provider migration complete.")
