#!/usr/bin/env bash
# staging-up.sh <branch> <image-ref> <image-digest>
# Spin up or refresh a staging environment on STAGING_DOCKER_HOST.
# image-ref: full image reference without digest, e.g. ghcr.io/myorg/codex-slack-master:v1.2.3
# image-digest: sha256:... digest from CI build
set -euo pipefail

: "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set}"
: "${REGISTRY:?REGISTRY must be set}"

BRANCH="${1:?Usage: staging-up.sh <branch> <image-ref> <image-digest>}"
IMAGE_REF="${2:?image-ref required}"
IMAGE_DIGEST="${3:?image-digest required}"

BRANCH_SLUG="$(echo "$BRANCH" | tr '/_' '-' | tr '[:upper:]' '[:lower:]')"

HOST_ADDR="${STAGING_DOCKER_HOST#ssh://}"
HOST_IP="$(getent hosts "${HOST_ADDR%%@*}" 2>/dev/null | awk '{print $1}' || echo "${HOST_ADDR##*@}")"
HOST_IP_DASHED="${HOST_IP//./-}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export DOCKER_HOST="$STAGING_DOCKER_HOST"
export BRANCH_SLUG
export HOST_IP_DASHED
export MASTER_RUNTIME_IMAGE="$IMAGE_REF"
export IMAGE_DIGEST="$IMAGE_DIGEST"
export MASTER_AGENT_NETWORK="${BRANCH_SLUG}_internal"

# Auto-detect docker socket GID from the remote host unless overridden.
if [ -z "${DOCKER_GID:-}" ]; then
  DOCKER_GID="$(docker run --rm -v /var/run/docker.sock:/sock alpine stat -c '%g' /sock 2>/dev/null)"
  echo "==> DOCKER_GID=${DOCKER_GID} (detected from ${HOST_ADDR})"
fi
export DOCKER_GID

echo "==> staging-up: branch=${BRANCH} slug=${BRANCH_SLUG} image=${IMAGE_REF}@${IMAGE_DIGEST}"

echo "==> Pulling image..."
docker pull "${IMAGE_REF}@${IMAGE_DIGEST}"

echo "==> Bringing up services..."
docker compose \
  -p "${BRANCH_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.staging.yml" \
  up -d --remove-orphans

echo "==> Waiting for master healthcheck..."
RETRIES=18
until docker compose \
  -p "${BRANCH_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.staging.yml" \
  exec master curl -sf http://localhost:8080/health > /dev/null 2>&1; do
  RETRIES=$((RETRIES - 1))
  if [ "$RETRIES" -eq 0 ]; then
    echo "ERROR: master failed healthcheck after 90s" >&2
    docker compose -p "${BRANCH_SLUG}" -f "${PROJECT_ROOT}/docker-compose.yml" \
      -f "${PROJECT_ROOT}/docker-compose.staging.yml" logs --tail=50 master >&2
    exit 1
  fi
  sleep 5
done

echo ""
echo "Staging env ready:"
echo "  master: http://master.${BRANCH_SLUG}.${HOST_IP_DASHED}.nip.io"
