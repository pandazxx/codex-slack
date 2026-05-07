#!/usr/bin/env bash
# traefik-down.sh — tear down the shared Traefik ingress stack.
# Does NOT delete the sre_ingress network if branch envs are still attached.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

export DOCKER_HOST="${DEV_DOCKER_HOST:-}"

echo "Stopping Traefik ingress..."
docker compose -p sre-ingress -f docker-compose.traefik.yml down

# Try to remove the shared network. If branch envs are still attached, this
# will fail with "has active endpoints" — that's expected and harmless. We
# leave it in place so branch envs keep working until they're torn down.
if docker network inspect sre_ingress >/dev/null 2>&1; then
  if docker network rm sre_ingress >/dev/null 2>&1; then
    echo "Removed shared ingress network: sre_ingress"
  else
    echo "Note: sre_ingress network kept (branch envs still attached). Tear down branch envs first."
  fi
fi

echo "Traefik ingress stopped."
