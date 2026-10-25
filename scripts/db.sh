#!/usr/bin/env bash
# Database management script for the master service.
#
# Usage: db.sh <subcommand> [options]
#
# Subcommands:
#   migrate                  Apply pending Alembic migrations (alembic upgrade head).
#   verify-schema            Compare live schema against Alembic head; exits non-zero on drift.
#   snapshot export          Export database tables to a JSONL snapshot directory.
#   snapshot import          Import a JSONL snapshot directory into a target database.
#   snapshot-and-migrate     Snapshot the source then migrate or import to target.
#   --help                   Show this help message.
#
# Environment:
#   DATABASE_URL     Full SQLAlchemy URL — highest priority.
#   MASTER_DATA_DIR  Used for snapshot default output path.
#   POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_DB, POSTGRES_PASSWORD
#                    Components for the bundled Postgres URL when DATABASE_URL is unset.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

_usage() {
    grep '^# ' "$0" | sed 's/^# \?//'
    exit 0
}

_python() {
    cd "${PROJECT_ROOT}"
    python3 -m src.master.db_cli "$@"
}

_migrate() {
    echo "db.sh: running alembic upgrade head"
    _python migrate
}

_verify_schema() {
    echo "db.sh: verifying schema against Alembic head"
    _python verify-schema
}

_snapshot() {
    _python snapshot "$@"
}

_snapshot_and_migrate() {
    _python snapshot-and-migrate "$@"
}

case "${1:-}" in
    --help|-h|help)
        _usage
        ;;
    migrate)
        shift
        _migrate "$@"
        ;;
    verify-schema)
        shift
        _verify_schema "$@"
        ;;
    snapshot)
        shift
        _snapshot "$@"
        ;;
    snapshot-and-migrate)
        shift
        _snapshot_and_migrate "$@"
        ;;
    "")
        echo "db.sh: subcommand required. Use --help for usage." >&2
        exit 1
        ;;
    *)
        echo "db.sh: unknown subcommand '${1}'. Use --help for usage." >&2
        exit 1
        ;;
esac
