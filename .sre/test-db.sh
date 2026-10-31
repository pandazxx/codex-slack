#!/usr/bin/env bash
# Run the pytest suite in the test image against one database backend.
#
# Usage: .sre/test-db.sh <sqlite|postgres|mysql> [pytest -k pattern]
#
# Used by .github/workflows/ci.yml (runner-local Docker) and `just test`
# (DOCKER_HOST=$DEV_DOCKER_HOST, exported by the recipe). It never selects a
# Docker host itself.
#
# sqlite    suite on the per-test SQLite default (conftest fallback).
# postgres  postgres:17-alpine (same pin as docker-compose.postgres.yml);
#           TEST_DATABASE_URL = whole suite, TEST_POSTGRES_URL = cross-dialect
#           db tests (a second database, because those tests DROP tables).
# mysql     mysql:8.0 (>= 8.0.16), same two-database layout. Phase 2 hardening
#           is pending, so CI treats this leg as non-blocking.
set -euo pipefail

DB="${1:-sqlite}"
PATTERN="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${TEST_PROJECT:-ci-tests-$$}"

# Scrub DB settings that `just` may have loaded from .env (staging values).
unset DATABASE_URL TEST_DATABASE_URL TEST_POSTGRES_URL TEST_MYSQL_URL \
      COMPOSE_PROFILES POSTGRES_PASSWORD

FILES=(-f "${ROOT}/docker-compose.yml" -f "${ROOT}/docker-compose.ci.yml")
RUN_ENV=()
# Throwaway credentials for ephemeral, unpublished containers.
PW="ci-test-only"

compose() { docker compose -p "${PROJECT}" "${FILES[@]}" "$@"; }

case "$DB" in
    sqlite)
        ;;
    postgres)
        FILES+=(-f "${ROOT}/docker-compose.postgres.yml")
        export COMPOSE_PROFILES="db-bundled" POSTGRES_PASSWORD="$PW"
        URL="postgresql+psycopg://master:${PW}@postgres:5432/master"
        XURL="postgresql+psycopg://master:${PW}@postgres:5432/master_xdialect"
        # DATABASE_URL is passed explicitly: with the postgres overlay layered
        # the key would otherwise exist but be empty, and conftest only falls
        # back to TEST_DATABASE_URL / per-test SQLite when the key is absent.
        # PGOPTIONS sets lock_timeout for every libpq/psycopg connection so that
        # a leaked "idle in transaction" connection from one test cannot silently
        # block the next one for more than 60 s (senior-sre review: see ci.yml).
        RUN_ENV=(
            -e "DATABASE_URL=${URL}"
            -e "TEST_DATABASE_URL=${URL}"
            -e "TEST_POSTGRES_URL=${XURL}"
            -e "PGOPTIONS=-c lock_timeout=60s"
        )
        ;;
    mysql)
        export COMPOSE_PROFILES="ci-mysql" MYSQL_PASSWORD="$PW"
        URL="mysql+pymysql://master:${PW}@mysql:3306/master"
        XURL="mysql+pymysql://master:${PW}@mysql:3306/master_xdialect"
        RUN_ENV=(-e "DATABASE_URL=${URL}" -e "TEST_DATABASE_URL=${URL}" -e "TEST_MYSQL_URL=${XURL}")
        ;;
    *)
        echo "ERROR: backend must be sqlite, postgres or mysql, got: ${DB}" >&2
        exit 2
        ;;
esac

cleanup() { compose down --volumes --remove-orphans 2>/dev/null || true; }
trap cleanup EXIT

echo "==> [${DB}] Building test image..."
compose build master

case "$DB" in
    postgres)
        echo "==> [postgres] Starting database..."
        compose up -d --wait postgres
        compose exec -T postgres createdb -U master master_xdialect
        ;;
    mysql)
        echo "==> [mysql] Starting database..."
        compose up -d --wait mysql
        # shellcheck disable=SC2016  # $MYSQL_ROOT_PASSWORD must expand inside the container
        compose exec -T mysql sh -c \
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE DATABASE master_xdialect; GRANT ALL ON master_xdialect.* TO '"'"'master'"'"'@'"'"'%'"'"';"'
        ;;
esac

echo "==> [${DB}] Running tests${PATTERN:+ (pattern: $PATTERN)}..."
compose run --rm "${RUN_ENV[@]}" master \
    python -m pytest ${PATTERN:+-k "$PATTERN"} -v --tb=short
