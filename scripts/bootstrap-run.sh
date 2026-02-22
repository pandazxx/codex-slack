#!/usr/bin/env bash
set -euo pipefail

# Run codex-slack bot container against a different host repository mounted as /workspace.
#
# Usage:
#   SLACK_BOT_TOKEN=... SLACK_APP_TOKEN=... SLACK_ALLOWED_CHANNELS=... \
#   [CODEX_SESSION_ID=...] [IMAGE_NAME=codex-slack-bot:latest] \
#   [CONTAINER_NAME=codex-slack-bot] \
#   ./scripts/bootstrap-run.sh /absolute/path/to/target-repo

TARGET_REPO="${1:-}"
if [[ -z "${TARGET_REPO}" ]]; then
  echo "Usage: $0 /absolute/path/to/target-repo"
  exit 1
fi

if [[ ! -d "${TARGET_REPO}/.git" ]]; then
  echo "Error: ${TARGET_REPO} is not a git repository"
  exit 1
fi

: "${SLACK_BOT_TOKEN:?SLACK_BOT_TOKEN is required}"
: "${SLACK_APP_TOKEN:?SLACK_APP_TOKEN is required}"
: "${SLACK_ALLOWED_CHANNELS:?SLACK_ALLOWED_CHANNELS is required}"

IMAGE_NAME="${IMAGE_NAME:-codex-slack-bot:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-codex-slack-bot}"

mkdir -p "${TARGET_REPO}/logs"
test -f "${HOME}/.codex/auth.json" || { echo "Missing ${HOME}/.codex/auth.json"; exit 1; }
test -d "${HOME}/.codex/sessions" || { echo "Missing ${HOME}/.codex/sessions"; exit 1; }

echo "[1/3] Building image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" .

echo "[2/3] Removing previous container (if any): ${CONTAINER_NAME}"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

CONTAINER_WORKSPACE_PATH="${CODEX_WORKSPACE_PATH:-/workspace}"
CONTAINER_LOG_FILE="${BOT_LOG_FILE:-${CONTAINER_WORKSPACE_PATH}/logs/bot.log}"

echo "[3/3] Starting container with mounted workspace: ${TARGET_REPO}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  -e SLACK_BOT_TOKEN \
  -e SLACK_APP_TOKEN \
  -e SLACK_ALLOWED_CHANNELS \
  -e CODEX_WORKSPACE_PATH="${CONTAINER_WORKSPACE_PATH}" \
  -e BOT_LOG_FILE="${CONTAINER_LOG_FILE}" \
  -e CODEX_SESSION_ID="${CODEX_SESSION_ID:-}" \
  -v "${TARGET_REPO}:${CONTAINER_WORKSPACE_PATH}" \
  -v "${TARGET_REPO}/logs:${CONTAINER_WORKSPACE_PATH}/logs" \
  -v "${HOME}/.codex/auth.json:/run/secrets/codex_auth.json:ro" \
  -v "${HOME}/.codex/sessions:/run/secrets/codex_sessions:ro" \
  "${IMAGE_NAME}"

echo "Container started."
echo "Follow logs:"
echo "  docker logs -f ${CONTAINER_NAME}"
