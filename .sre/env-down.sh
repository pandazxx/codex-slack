#!/usr/bin/env bash
# env-down.sh <branch>
# Tear down a dev environment for the given branch on DEV_DOCKER_HOST.
set -euo pipefail

: "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

BRANCH="${1:?Usage: env-down.sh <branch>}"
BRANCH_SLUG="$(echo "$BRANCH" | tr '/_' '-' | tr '[:upper:]' '[:lower:]')"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export DOCKER_HOST="$DEV_DOCKER_HOST"

echo "==> env-down: branch=${BRANCH} slug=${BRANCH_SLUG}"
docker compose \
  -p "${BRANCH_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.override.yml" \
  down --volumes --remove-orphans

echo "==> Dev env ${BRANCH_SLUG} torn down."
