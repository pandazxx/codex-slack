#!/usr/bin/env bash
# Database management script for master service.
#
# Usage: db.sh <subcommand> [options]
#
# Subcommands:
#   migrate              Apply pending Alembic migrations (alembic upgrade head).
#   verify-schema        Compare live schema against Alembic head; exits non-zero on drift.
#   snapshot-and-migrate Take a backup snapshot then migrate (not implemented in M0).
#   --help               Show this help message.
#
# Environment:
#   DATABASE_URL     Full SQLAlchemy URL — highest priority.
#   MASTER_DATA_DIR  Fallback; resolves to sqlite:///{MASTER_DATA_DIR}/master.db.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

_usage() {
    grep '^# ' "$0" | sed 's/^# \?//'
    exit 0
}

_alembic() {
    # Run alembic from the project root so it picks up alembic.ini.
    cd "${PROJECT_ROOT}"
    python3 -m alembic "$@"
}

_migrate() {
    echo "db.sh: running alembic upgrade head"
    _alembic upgrade head
    echo "db.sh: migrations complete"
}

_verify_schema() {
    # alembic check exits non-zero when the database is not at the head revision.
    echo "db.sh: verifying schema against Alembic head"
    if ! _alembic check 2>&1; then
        echo "db.sh: DRIFT DETECTED — database schema does not match Alembic head" >&2
        exit 1
    fi
    echo "db.sh: schema is up to date"
}

_snapshot_and_migrate() {
    echo "db.sh: snapshot-and-migrate is not implemented in M0" >&2
    echo "db.sh: Run 'db.sh migrate' to apply migrations without a snapshot." >&2
    exit 1
}

case "${1:-}" in
    --help|-h|help)
        _usage
        ;;
    migrate)
        _migrate
        ;;
    verify-schema)
        _verify_schema
        ;;
    snapshot-and-migrate)
        _snapshot_and_migrate
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
