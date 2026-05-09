#!/usr/bin/env bash
# run-tests.sh [<pytest-pattern>]
# Run tests inside the test-stage image on DEV_DOCKER_HOST.
# Builds docker-compose.ci.yml (target: test) then runs pytest.
set -euo pipefail

: "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

PATTERN="${1:-}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST_PROJECT="ci-tests-$$"

export DOCKER_HOST="$DEV_DOCKER_HOST"

cleanup() {
  docker compose \
    -p "${TEST_PROJECT}" \
    -f "${PROJECT_ROOT}/docker-compose.yml" \
    -f "${PROJECT_ROOT}/docker-compose.ci.yml" \
    down --volumes --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Building test image..."
docker compose \
  -p "${TEST_PROJECT}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.ci.yml" \
  build master

echo "==> Running tests${PATTERN:+ (pattern: $PATTERN)}..."
docker compose \
  -p "${TEST_PROJECT}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.ci.yml" \
  run --rm master \
  python -m pytest ${PATTERN:+-k "$PATTERN"} -v --tb=short
