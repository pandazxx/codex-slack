"""CLI entry point for database management commands.

Invoked by scripts/db.sh as: python -m src.master.db_cli <subcommand> [options]

Subcommands:
  snapshot export  --source-url URL --out DIR
  snapshot import  --in DIR --target-url URL [--yes]
  snapshot-and-migrate [--source-url URL] [--target-url URL] [--out DIR]
  migrate
  verify-schema
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parents[2]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="db_cli",
        description="Master database management tool.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # snapshot (parent parser — snapshot itself has sub-subcommands)
    snap = sub.add_parser("snapshot", help="Export or import a database snapshot.")
    snap_sub = snap.add_subparsers(dest="snap_command", metavar="SNAP_COMMAND")

    # snapshot export
    exp = snap_sub.add_parser(
        "export",
        help="Export database tables to JSONL snapshot files.",
    )
    exp.add_argument(
        "--source-url",
        required=True,
        metavar="URL",
        help="SQLAlchemy URL of the source database.",
    )
    exp.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Output directory for the snapshot files.",
    )

    # snapshot import
    imp = snap_sub.add_parser(
        "import",
        help="Import a JSONL snapshot into a target database.",
    )
    imp.add_argument(
        "--in",
        dest="in_dir",
        required=True,
        metavar="DIR",
        help="Input directory containing manifest.json and JSONL files.",
    )
    imp.add_argument(
        "--target-url",
        required=True,
        metavar="URL",
        help="SQLAlchemy URL of the target database.",
    )
    imp.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Truncate and reload even if the target already has rows.",
    )
    imp.add_argument(
        "--lock-timeout",
        dest="lock_timeout",
        default="30s",
        metavar="TIMEOUT",
        help=(
            "Lock-acquisition timeout for Postgres TRUNCATE (e.g. '30s', '2min'). "
            "Fails fast with a clear error if the target DB is held by another "
            "connection.  Pass '0' to disable (not recommended)."
        ),
    )

    # snapshot-and-migrate
    sam = sub.add_parser(
        "snapshot-and-migrate",
        help=(
            "Take a snapshot of the source database, then either upgrade it "
            "in-place (same URL) or import the snapshot into a new target "
            "(different URL — provider migration)."
        ),
    )
    sam.add_argument(
        "--source-url",
        metavar="URL",
        default="",
        help="Source database URL (default: DATABASE_URL env var).",
    )
    sam.add_argument(
        "--target-url",
        metavar="URL",
        default="",
        help=(
            "Target database URL for provider migration.  "
            "Omit (or set equal to --source-url) for in-place upgrade."
        ),
    )
    sam.add_argument(
        "--out",
        metavar="DIR",
        default="",
        help=(
            "Snapshot output directory.  "
            "Default: ${MASTER_DATA_DIR}/snapshots/<timestamp>/ or "
            "./snapshots/<timestamp>/."
        ),
    )

    # migrate
    sub.add_parser("migrate", help="Run alembic upgrade head on the configured database.")

    # verify-schema
    sub.add_parser("verify-schema", help="Check the database schema against the Alembic head.")

    return parser


def _source_url_from_env(override: str) -> str:
    if override:
        return override
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    print("ERROR: --source-url not given and DATABASE_URL is not set.", file=sys.stderr)
    sys.exit(1)


def _default_snapshot_dir() -> Path:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data_dir = os.environ.get("MASTER_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir) / "snapshots" / ts
    return Path(".") / "snapshots" / ts


def _cmd_snapshot_export(args: argparse.Namespace) -> None:
    from src.master.db.snapshot import export_snapshot
    out_dir = Path(args.out)
    export_snapshot(args.source_url, out_dir, REPO_ROOT)


def _cmd_snapshot_import(args: argparse.Namespace) -> None:
    from src.master.db.snapshot import import_snapshot
    in_dir = Path(args.in_dir)
    import_snapshot(
        in_dir, args.target_url, REPO_ROOT,
        yes=args.yes,
        lock_timeout=args.lock_timeout,
    )


def _cmd_snapshot_and_migrate(args: argparse.Namespace) -> None:
    from src.master.db.snapshot import snapshot_and_migrate
    source_url = _source_url_from_env(args.source_url)
    target_url = args.target_url.strip() or None
    out_dir = Path(args.out) if args.out.strip() else _default_snapshot_dir()
    snapshot_and_migrate(source_url, target_url, out_dir, REPO_ROOT)


def _stamp_prebaseline_sqlite_if_needed(url: str, cfg: Any) -> None:
    """Apply pre-baseline fixups and stamp at the baseline revision for a SQLite DB.

    No-op when the DB is not SQLite, not a file-based DB, or already has an
    alembic_version entry (i.e. not pre-baseline).

    Pre-baseline means: the 'workspaces' table is present but 'alembic_version'
    is absent.  This is the state of SQLite installs that pre-date the Alembic
    migration in M1.  The operator is expected to run 'scripts/db.sh migrate'
    (with DATABASE_URL pointing at the SQLite file) to bring the source up to
    the current schema before exporting.
    """
    if not url or not url.startswith("sqlite"):
        return

    from sqlalchemy.engine import make_url as _sa_make_url
    db_str = str(_sa_make_url(url).database or "")
    if not db_str or db_str == ":memory:":
        return

    from sqlalchemy import inspect as sa_inspect
    from alembic.runtime.migration import MigrationContext
    from alembic import command as alembic_command
    from src.master.db import _make_engine, _with_database_url
    from src.master.db._prebaseline import init_db_sqlite

    eng = _make_engine(url)
    try:
        insp = sa_inspect(eng)
        table_names = set(insp.get_table_names())
        has_workspaces = "workspaces" in table_names
        has_alembic_version = "alembic_version" in table_names

        if not has_workspaces or has_alembic_version:
            return  # empty DB or already Alembic-stamped; let alembic handle it
    finally:
        eng.dispose()

    print(
        "db_cli: pre-baseline SQLite detected — applying pre-baseline fixups "
        "and stamping at baseline revision 000000000001"
    )
    init_db_sqlite(db_str)
    _with_database_url(url, lambda: alembic_command.stamp(cfg, "000000000001"))
    print("db_cli: pre-baseline stamp complete; running alembic upgrade head for any newer migrations")


def _cmd_migrate() -> None:
    from alembic.config import Config
    from alembic import command as alembic_command

    cfg = Config(str(REPO_ROOT / "alembic.ini"))

    # Handle pre-baseline SQLite before alembic upgrade runs.  If DATABASE_URL
    # points at a SQLite file that pre-dates Alembic, stamp it at the baseline
    # first so that 'alembic upgrade head' can then walk forward from there.
    url = os.environ.get("DATABASE_URL", "").strip()
    _stamp_prebaseline_sqlite_if_needed(url, cfg)

    print("db_cli: running alembic upgrade head")
    alembic_command.upgrade(cfg, "head")
    print("db_cli: migrations complete")


def _cmd_verify_schema() -> None:
    from alembic.config import Config
    from alembic import command as alembic_command

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    print("db_cli: verifying schema against Alembic head")
    try:
        alembic_command.check(cfg)
    except Exception as exc:
        print(f"DRIFT DETECTED: {exc}", file=sys.stderr)
        sys.exit(1)
    print("db_cli: schema is up to date")


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        if args.snap_command == "export":
            _cmd_snapshot_export(args)
        elif args.snap_command == "import":
            _cmd_snapshot_import(args)
        else:
            parser.parse_args(["snapshot", "--help"])
            sys.exit(1)
    elif args.command == "snapshot-and-migrate":
        _cmd_snapshot_and_migrate(args)
    elif args.command == "migrate":
        _cmd_migrate()
    elif args.command == "verify-schema":
        _cmd_verify_schema()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
