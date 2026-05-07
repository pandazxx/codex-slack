#!/usr/bin/env bash
# env-down.sh — tear down a dev environment.
# Called by the SRE subagent; called by humans to clean up when done.
#
# Usage:
#   env-down.sh [BRANCH_SLUG]
#
# Removes containers, volumes, and the laptop /etc/hosts entry for this branch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

export DOCKER_HOST="${DEV_DOCKER_HOST:-}"

if [[ -n "${1:-}" ]]; then
  BRANCH_SLUG="$1"
else
  BRANCH_NAME=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
  BRANCH_SLUG=$(echo "$BRANCH_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-*$//' | cut -c1-32)
fi

PROJECT_NAME="${USER}-${BRANCH_SLUG}"
INGRESS_HOST="master.${BRANCH_SLUG}.dev.docker-testbed.local"

echo "Tearing down environment: $PROJECT_NAME"

docker compose -p "$PROJECT_NAME" down -v --remove-orphans

# Remove the laptop /etc/hosts entry for this branch (Traefik-mode artifact).
# Hosts entries are idempotent — if it isn't present, nothing happens.
if grep -qE "^[[:space:]]*[0-9.]+[[:space:]]+${INGRESS_HOST}([[:space:]]|$)" /etc/hosts 2>/dev/null; then
  if sudo -n true 2>/dev/null; then
    # macOS sed needs the empty -i argument; covers both BSD and GNU.
    sudo sed -i.bak "/[[:space:]]${INGRESS_HOST}[[:space:]]/d" /etc/hosts \
      && sudo rm -f /etc/hosts.bak
    echo "Removed /etc/hosts entry: ${INGRESS_HOST}"
  else
    echo "NOTE: /etc/hosts contains an entry for ${INGRESS_HOST} but sudo is not"
    echo "      available non-interactively. Remove it manually:"
    echo "  sudo sed -i '' \"/[[:space:]]${INGRESS_HOST}[[:space:]]/d\" /etc/hosts"
  fi
fi

echo "Environment torn down: $PROJECT_NAME"
