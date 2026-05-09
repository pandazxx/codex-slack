#!/usr/bin/env bash
# staging-down.sh <image-ref>
# Tear down a staging environment on STAGING_DOCKER_HOST.
# image-ref: full image reference used at spin-up time, e.g. ghcr.io/myorg/codex-slack-master:v4.7-rc2
# The compose project name (VERSION_SLUG) is re-derived from the image tag, matching staging-up.sh.
set -euo pipefail

: "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set}"

IMAGE_REF="${1:?Usage: staging-down.sh <image-ref>}"

# Derive VERSION_SLUG the same way staging-up.sh does.
IMAGE_TAG="${IMAGE_REF##*:}"
VERSION_SLUG="$(echo "$IMAGE_TAG" | tr './_' '-' | tr '[:upper:]' '[:lower:]')"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export DOCKER_HOST="$STAGING_DOCKER_HOST"

echo "==> staging-down: version=${IMAGE_TAG} slug=${VERSION_SLUG}"
docker compose \
  -p "${VERSION_SLUG}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  -f "${PROJECT_ROOT}/docker-compose.staging.yml" \
  down --volumes --remove-orphans

echo "==> Staging env ${VERSION_SLUG} torn down."
