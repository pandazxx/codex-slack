#!/usr/bin/env bash
# Sourced by justfile recipes (not run directly). Resolves the database
# settings that docker-compose.postgres.yml interpolates.
#
# .env is shared by every environment on the operator's machine, so dev and
# singleton recipes must not read the same DB settings from it:
#   dev       -> always bundled Postgres, one per branch stack (project-scoped
#                volume + network). .env DB settings are ignored.
#   singleton -> COMPOSE_PROFILES / DATABASE_URL / POSTGRES_PASSWORD come from
#                the caller's shell or .env (shell wins, per `just` dotenv-load).

_sre_blank() { [[ -z "${1//[[:space:]]/}" ]]; }

# Dev shape: bundled Postgres with a dev-only password (override with
# DEV_POSTGRES_PASSWORD). DATABASE_URL is forced empty (exported empty beats
# the .env file that Compose itself also reads) so a staging/managed URL in
# .env can never leak into a branch stack.
sre_db_env_dev() {
    export COMPOSE_PROFILES="db-bundled"
    export POSTGRES_PASSWORD="${DEV_POSTGRES_PASSWORD:-master-dev-only}"
    export DATABASE_URL=""
}

# Singleton shape (staging/prod): pass the operator's settings through.
sre_db_env_singleton() {
    export COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"
    export DATABASE_URL="${DATABASE_URL:-}"
    export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
}

# Fail before touching the host when the database configuration is incomplete.
sre_db_require_config() {
    case ",${COMPOSE_PROFILES// /}," in
        *,db-bundled,*)
            if _sre_blank "${POSTGRES_PASSWORD:-}"; then
                echo "ERROR: COMPOSE_PROFILES includes db-bundled but POSTGRES_PASSWORD is empty." >&2
                echo "       Set POSTGRES_PASSWORD in .env (see .env.example)." >&2
                return 1
            fi
            ;;
        *)
            if _sre_blank "${DATABASE_URL:-}"; then
                echo "ERROR: no database configured. Either:" >&2
                echo "  - bundled Postgres: COMPOSE_PROFILES=db-bundled and POSTGRES_PASSWORD=<secret>, or" >&2
                echo "  - managed/SQLite:   DATABASE_URL=<sqlalchemy url> (leave COMPOSE_PROFILES unset)." >&2
                echo "  See .env.example and .sre/operations/deploy.md." >&2
                return 1
            fi
            ;;
    esac
}

# Refuse to replace a working singleton with a master that cannot start:
# if the stack's master_data volume holds a legacy SQLite file and DATABASE_URL
# is blank, master exits at boot (design section 11). Read-only probe.
# $1 = DOCKER_HOST of the target. Skips silently on first deploy (no volume).
sre_db_legacy_sqlite_guard() {
    local host="$1" vol="codex-slack_master_data"
    _sre_blank "${DATABASE_URL:-}" || return 0
    DOCKER_HOST="$host" docker volume inspect "$vol" >/dev/null 2>&1 || return 0
    if DOCKER_HOST="$host" docker run --rm -v "${vol}:/data:ro" alpine \
            test -s /data/master_data.db 2>/dev/null; then
        echo "ERROR: ${vol} contains a legacy SQLite database (master_data.db) and DATABASE_URL is empty." >&2
        echo "       master would refuse to start and the running stack would be replaced by a crash loop." >&2
        echo "       Keep SQLite (DATABASE_URL=sqlite+pysqlite:////opt/codex-slack/data/master/master_data.db)" >&2
        echo "       or run the cut-over in .sre/operations/deploy.md first." >&2
        return 1
    fi
}
