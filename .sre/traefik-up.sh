#!/usr/bin/env bash
# traefik-up.sh — bring up the shared Traefik ingress stack on the dev Docker host.
# Idempotent — safe to call repeatedly. Called once per host, not per branch.
#
# Honors DEV_DOCKER_HOST if set (remote testbed). Otherwise local Docker.
#
# See docs/sre-decisions/2026-05-07-traefik-multi-env-routing.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

export DOCKER_HOST="${DEV_DOCKER_HOST:-}"

# Ensure the shared ingress network exists. `external: true` in the compose
# file means compose will not create it; we own its lifecycle.
if ! docker network inspect sre_ingress >/dev/null 2>&1; then
  echo "Creating shared ingress network: sre_ingress"
  docker network create sre_ingress >/dev/null
else
  echo "Shared ingress network already present: sre_ingress"
fi

# Bring up Traefik. Fixed project name so it's not tangled with any branch.
echo "Starting Traefik ingress (project: sre-ingress)..."
docker compose -p sre-ingress -f docker-compose.traefik.yml up -d

# Wait for the dashboard endpoint to come up — proxies that the static config
# parsed and the listener is bound.
echo "Waiting for Traefik to become ready..."
attempt=0
max_attempts=15
ready=0
while (( attempt < max_attempts )); do
  if docker compose -p sre-ingress -f docker-compose.traefik.yml ps traefik 2>/dev/null \
       | grep -qE "healthy|running"; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if (( ready == 0 )); then
  echo "ERROR: Traefik did not come up. Logs:"
  docker compose -p sre-ingress -f docker-compose.traefik.yml logs traefik
  exit 1
fi

cat <<EOF

Traefik ingress is up.
  Project:        sre-ingress
  Dashboard:      http://traefik.dev.docker-testbed.local  (after env-up adds /etc/hosts entry)
  Network:        sre_ingress (external, shared by all branch envs)

To register a branch env: .sre/env-up.sh [BRANCH_SLUG]
To tear down ingress:     .sre/traefik-down.sh
EOF
