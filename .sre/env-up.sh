#!/usr/bin/env bash
# env-up.sh — spin up a dev environment for a branch
# Called by the SRE subagent; called by humans only to resume a stopped env.
#
# Environment variables:
#   DOCKER_HOST (optional) — remote Docker host (e.g., ssh://ubuntu@10.10.10.238)
#   DOCKER_GID (optional) — group ID of Docker daemon on remote host (required if DOCKER_HOST is set)
#
# Usage:
#   env-up.sh [BRANCH_SLUG]
#
# If BRANCH_SLUG is omitted, uses current git branch (sanitized).
# Environment is idempotent: called twice on the same branch returns the
# existing env instead of creating a duplicate.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# Derive branch slug from argument or current git branch.
if [[ -n "${1:-}" ]]; then
  BRANCH_SLUG="$1"
else
  BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD)
  # Sanitize: replace non-alphanumeric with hyphen, lowercase, max 32 chars.
  BRANCH_SLUG=$(echo "$BRANCH_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-*$//' | cut -c1-32)
fi

# Derive project name and env file path.
PROJECT_NAME="${USER}-${BRANCH_SLUG}"
ENV_FILE="${REPO_ROOT}/.env.local.${BRANCH_SLUG}"

echo "Setting up dev environment: $PROJECT_NAME"

# Check if environment already exists.
if docker compose -p "$PROJECT_NAME" ls &>/dev/null; then
  echo "Environment already running. Bringing it up..."
  COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml \
    docker compose -p "$PROJECT_NAME" up -d
  docker compose -p "$PROJECT_NAME" ps
  exit 0
fi

# Create .env.local if it doesn't exist (allows overrides without committing).
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'EOF'
# Local overrides for this branch — never commit.
# Uncomment and set API keys if testing integrations:
# ANTHROPIC_API_KEY=sk-...
# GH_TOKEN=ghp_...
# OPENAI_API_KEY=sk-...
EOF
  echo "Created $ENV_FILE — add API keys there if needed."
fi

# Start the stack.
echo "Starting services for $PROJECT_NAME..."
COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml \
  docker compose -p "$PROJECT_NAME" up -d

# Wait for health checks.
echo "Waiting for services to be healthy..."
max_retries=30
attempt=0
while (( attempt < max_retries )); do
  if docker compose -p "$PROJECT_NAME" ps master | grep -q "healthy\|running"; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if ! docker compose -p "$PROJECT_NAME" ps master | grep -q "healthy\|running"; then
  echo "ERROR: master service failed to start. Logs:"
  docker compose -p "$PROJECT_NAME" logs master
  exit 1
fi

echo "Environment ready: $PROJECT_NAME"
docker compose -p "$PROJECT_NAME" ps

# Print access information.
cat <<EOF

Access the environment:
  Web UI:       http://localhost:8080
  API docs:     http://localhost:8080/docs
  Health:       http://localhost:8080/health
  Logs:         docker compose -p $PROJECT_NAME logs -f master

Direct access:
  Mosquitto:    docker compose -p $PROJECT_NAME exec mosquitto mosquitto_sub -h localhost -t '#' -v
  Master shell: docker compose -p $PROJECT_NAME exec -it master bash

To stop:     .sre/env-down.sh $BRANCH_SLUG
To see logs: docker compose -p $PROJECT_NAME logs -f [SERVICE]
EOF
