#!/usr/bin/env bash
# env-status.sh
# List all active compose projects on DEV_DOCKER_HOST and STAGING_DOCKER_HOST.
set -euo pipefail

: "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

echo "=== DEV (${DEV_DOCKER_HOST}) ==="
DOCKER_HOST="$DEV_DOCKER_HOST" docker compose ls 2>/dev/null || echo "  (no projects or unreachable)"

if [ -n "${STAGING_DOCKER_HOST:-}" ]; then
  echo ""
  echo "=== STAGING (${STAGING_DOCKER_HOST}) ==="
  DOCKER_HOST="$STAGING_DOCKER_HOST" docker compose ls 2>/dev/null || echo "  (no projects or unreachable)"
else
  echo ""
  echo "=== STAGING === (STAGING_DOCKER_HOST not set — skipped)"
fi
