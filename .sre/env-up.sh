#!/usr/bin/env bash
# env-up.sh — spin up a dev environment for a branch
# Called by the SRE subagent; called by humans only to resume a stopped env.
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

# Honor DEV_DOCKER_HOST if set; otherwise use local Docker.
# This allows the script to target remote dev infrastructure.
export DOCKER_HOST="${DEV_DOCKER_HOST:-}"

# Determine which compose files to use.
# Remote Docker hosts (SSH) can't use bind mounts from localhost, so we use
# docker-compose.remote.yml which builds a full dev image instead.
# Local Docker uses docker-compose.override.yml with bind mounts for live reload.
if [[ -n "$DEV_DOCKER_HOST" ]]; then
  COMPOSE_OVERRIDE="docker-compose.remote.yml"
else
  COMPOSE_OVERRIDE="docker-compose.override.yml"
fi

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

# Derive a deterministic port for this project using hash of project name.
# Base port 8080 + offset (0-999) to avoid collisions when multiple envs run.
# This allows parallel feature-branch envs on the same Docker host.
PORT_OFFSET=$(($(echo -n "$PROJECT_NAME" | md5sum | head -c 3 | xargs -I {} printf '%d' 0x{}) % 900))
MASTER_PORT=$((8080 + PORT_OFFSET))

echo "Setting up dev environment: $PROJECT_NAME"

# Check if environment already exists (has running containers).
CONTAINER_COUNT=$(docker compose -p "$PROJECT_NAME" ps -q 2>/dev/null | wc -l)
if [[ "$CONTAINER_COUNT" -gt 0 ]]; then
  echo "Environment already running. Bringing it up..."
  COMPOSE_FILE="docker-compose.yml:$COMPOSE_OVERRIDE" \
    docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" up -d
  docker compose -p "$PROJECT_NAME" ps
  exit 0
fi

# Create .env.local if it doesn't exist (allows overrides without committing).
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# Local overrides for this branch — never commit.
# Uncomment and set API keys if testing integrations:
# ANTHROPIC_API_KEY=sk-...
# GH_TOKEN=ghp_...
# OPENAI_API_KEY=sk-...

# Container names and port scoped to this project (prevents collisions when
# running multiple envs with explicit container_name in docker-compose.yml).
MASTER_CONTAINER_NAME=${PROJECT_NAME}-master
MOSQUITTO_CONTAINER_NAME=${PROJECT_NAME}-mosquitto
MASTER_PORT=${MASTER_PORT}
EOF
  echo "Created $ENV_FILE — add API keys there if needed."
fi

# Set container name and port overrides; export them so docker compose picks them up.
export MASTER_CONTAINER_NAME="${PROJECT_NAME}-master"
export MOSQUITTO_CONTAINER_NAME="${PROJECT_NAME}-mosquitto"
export MASTER_PORT

# Source the .env.local file to apply any user overrides (API keys, etc).
set -a
# shellcheck disable=SC1091
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
set +a

# Build the dev image on the remote if using a remote Docker host.
# The dev image includes source code baked in since bind mounts won't work over SSH.
if [[ -n "$DEV_DOCKER_HOST" ]]; then
  echo "Building dev image on remote Docker host..."
  docker build -t codex-slack-master:dev -f Dockerfile.dev .
fi

# Start the stack. Pass the .env file explicitly to docker compose so it picks up
# MASTER_PORT and container name overrides. This ensures each dev env gets its own port.
echo "Starting services for $PROJECT_NAME..."
echo "Using compose override: $COMPOSE_OVERRIDE"
COMPOSE_FILE="docker-compose.yml:$COMPOSE_OVERRIDE" \
  docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" up -d

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
  Web UI:       http://localhost:${MASTER_PORT}
  API docs:     http://localhost:${MASTER_PORT}/docs
  Health:       http://localhost:${MASTER_PORT}/health
  Logs:         export DOCKER_HOST=\$DEV_DOCKER_HOST && docker compose -p $PROJECT_NAME logs -f master

Direct access (requires SSH tunnel):
  Mosquitto:    export DOCKER_HOST=\$DEV_DOCKER_HOST && docker compose -p $PROJECT_NAME exec mosquitto mosquitto_sub -h localhost -t '#' -v
  Master shell: export DOCKER_HOST=\$DEV_DOCKER_HOST && docker compose -p $PROJECT_NAME exec -it master bash

To access from your machine:
  ssh -L ${MASTER_PORT}:localhost:${MASTER_PORT} ubuntu@docker-testbed.local

To stop:     .sre/env-down.sh $BRANCH_SLUG
To see logs: export DOCKER_HOST=\$DEV_DOCKER_HOST && docker compose -p $PROJECT_NAME logs -f [SERVICE]
EOF
