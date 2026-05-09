#!/usr/bin/env bash
# env-up.sh <branch>
# Spin up or refresh a dev environment for the given branch on DEV_DOCKER_HOST.
# No source bind-mounts. Source is baked into the image at build time.
set -euo pipefail

: "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

BRANCH="${1:?Usage: env-up.sh <branch>}"
BRANCH_SLUG="$(echo "$BRANCH" | tr '/_' '-' | tr '[:upper:]' '[:lower:]')"

# Derive host IP for nip.io routing.
HOST_ADDR="${DEV_DOCKER_HOST#ssh://}"
HOST_IP="$(getent hosts "${HOST_ADDR%%@*}" 2>/dev/null | awk '{print $1}' || echo "${HOST_ADDR##*@}")"
HOST_IP_DASHED="${HOST_IP//./-}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export DOCKER_HOST="$DEV_DOCKER_HOST"
export BRANCH_SLUG
export HOST_IP_DASHED

echo "==> env-up: branch=${BRANCH} slug=${BRANCH_SLUG} host=${DEV_DOCKER_HOST}"
echo "==> Building image (target: dev)..."
docker compose \
  -p "${BRANCH_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.override.yml" \
  build master

echo "==> Bringing up services..."
docker compose \
  -p "${BRANCH_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.override.yml" \
  up -d --remove-orphans

echo "==> Waiting for master healthcheck..."
RETRIES=18
until docker compose \
  -p "${BRANCH_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.override.yml" \
  exec master curl -sf http://localhost:8080/health > /dev/null 2>&1; do
  RETRIES=$((RETRIES - 1))
  if [ "$RETRIES" -eq 0 ]; then
    echo "ERROR: master failed healthcheck after 90s" >&2
    docker compose -p "${BRANCH_SLUG}" -f "${PROJECT_ROOT}/docker-compose.yml" \
      -f "${PROJECT_ROOT}/docker-compose.override.yml" logs --tail=50 master >&2
    exit 1
  fi
  sleep 5
done

echo ""
echo "Dev env ready:"
echo "  master: http://master.${BRANCH_SLUG}.${HOST_IP_DASHED}.nip.io"
echo ""
echo "Exec into services:"
echo "  DOCKER_HOST=${DEV_DOCKER_HOST} docker compose -p ${BRANCH_SLUG} exec master bash"
echo "  DOCKER_HOST=${DEV_DOCKER_HOST} docker compose -p ${BRANCH_SLUG} exec mosquitto sh"
