#!/usr/bin/env bash
# test.sh — run unit and in-process tests in a container
# Called by the SRE subagent; called by humans for fast local test feedback.
#
# Usage:
#   test.sh [PYTEST_ARGS]
#
# Examples:
#   test.sh                                      # Run all tests
#   test.sh tests/test_version.py                # Run a single test file
#   test.sh -k test_image_contract               # Run tests matching pattern
#   test.sh -vv --tb=long                        # Verbose output, longer tracebacks

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# Ensure DOCKER_GID is set if using a remote Docker host (required for permission mapping).
if [[ -n "${DOCKER_HOST:-}" ]] && [[ "${DOCKER_HOST}" == ssh://* ]]; then
  if [[ -z "${DOCKER_GID:-}" ]]; then
    echo "WARNING: DOCKER_HOST is remote but DOCKER_GID is not set. This may cause permission errors."
    echo "Set DOCKER_GID to your Docker daemon's group ID on the remote host (usually 988 or 999)."
  fi
fi

# Build test image if not present or stale.
echo "Building test image..."
docker compose -f docker-compose.yml -f docker-compose.ci.yml build master

# Run tests.
echo "Running tests..."
docker compose -f docker-compose.yml -f docker-compose.ci.yml \
  run --rm master \
  pytest -vv tests/ --tb=short "$@"
