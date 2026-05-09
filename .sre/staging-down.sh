#!/usr/bin/env bash
# staging-down.sh <branch>
# Tear down a staging environment on STAGING_DOCKER_HOST.
set -euo pipefail

: "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set}"

BRANCH="${1:?Usage: staging-down.sh <branch>}"
BRANCH_SLUG="$(echo "$BRANCH" | tr '/_' '-' | tr '[:upper:]' '[:lower:]')"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export DOCKER_HOST="$STAGING_DOCKER_HOST"

echo "==> staging-down: branch=${BRANCH} slug=${BRANCH_SLUG}"
docker compose \
  -p "${BRANCH_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.staging.yml" \
  down --volumes --remove-orphans

echo "==> Staging env ${BRANCH_SLUG} torn down."
