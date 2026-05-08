#!/usr/bin/env bash
# env-down.sh — tear down a dev environment
# Called by the SRE subagent; called by humans to clean up when done.
#
# Environment variables:
#   DOCKER_HOST (optional) — remote Docker host (e.g., ssh://ubuntu@10.10.10.238)
#
# Usage:
#   env-down.sh [BRANCH_SLUG]
#
# If BRANCH_SLUG is omitted, uses current git branch (sanitized).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Derive branch slug from argument or current git branch.
if [[ -n "${1:-}" ]]; then
  BRANCH_SLUG="$1"
else
  BRANCH_NAME=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
  # Sanitize: replace non-alphanumeric with hyphen, lowercase, max 32 chars.
  BRANCH_SLUG=$(echo "$BRANCH_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-*$//' | cut -c1-32)
fi

PROJECT_NAME="${USER}-${BRANCH_SLUG}"

echo "Tearing down environment: $PROJECT_NAME"

# Tear down the stack (remove containers and volumes).
docker compose -p "$PROJECT_NAME" down -v --remove-orphans

echo "Environment torn down: $PROJECT_NAME"
