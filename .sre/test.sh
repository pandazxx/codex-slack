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

# NOTE: Tests run against LOCAL docker, not DEV_DOCKER_HOST.
# Unit and in-process tests don't need remote infrastructure.
# Use local Docker for fast feedback; stack tests go to dev env spun up by SRE.

# Build test image if not present or stale.
echo "Building test image..."
docker compose -f docker-compose.yml -f docker-compose.ci.yml build master

# Run tests.
echo "Running tests..."
docker compose -f docker-compose.yml -f docker-compose.ci.yml \
  run --rm master \
  pytest -vv tests/ --tb=short "$@"
