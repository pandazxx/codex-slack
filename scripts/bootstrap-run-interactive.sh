#!/usr/bin/env bash
set -euo pipefail

prompt_required() {
  local label="$1"
  local value=""
  while [[ -z "${value}" ]]; do
    read -r -p "${label}: " value
    if [[ -z "${value}" ]]; then
      echo "Value is required."
    fi
  done
  printf '%s' "${value}"
}

prompt_required_secret() {
  local label="$1"
  local value=""
  while [[ -z "${value}" ]]; do
    read -r -s -p "${label}: " value
    echo
    if [[ -z "${value}" ]]; then
      echo "Value is required."
    fi
  done
  printf '%s' "${value}"
}

prompt_optional() {
  local label="$1"
  local default_value="${2:-}"
  local value=""
  read -r -p "${label} [${default_value}]: " value
  if [[ -z "${value}" ]]; then
    value="${default_value}"
  fi
  printf '%s' "${value}"
}

prompt_optional_blank() {
  local label="$1"
  local value=""
  read -r -p "${label} (optional, press Enter to skip): " value
  printf '%s' "${value}"
}

echo "Interactive bootstrap for codex-slack container"
echo

DEFAULT_TARGET_REPO="$(pwd)"
TARGET_REPO="$(prompt_optional "Target repo absolute path" "${DEFAULT_TARGET_REPO}")"
if [[ ! -d "${TARGET_REPO}/.git" ]]; then
  echo "Error: ${TARGET_REPO} is not a git repository."
  exit 1
fi

SLACK_BOT_TOKEN="$(prompt_required_secret "SLACK_BOT_TOKEN")"
SLACK_APP_TOKEN="$(prompt_required_secret "SLACK_APP_TOKEN")"
SLACK_ALLOWED_CHANNELS="$(prompt_required "SLACK_ALLOWED_CHANNELS (comma-separated channel IDs)")"

DEFAULT_IMAGE_NAME="${IMAGE_NAME:-codex-slack-bot:latest}"
DEFAULT_CONTAINER_NAME="${CONTAINER_NAME:-codex-slack-bot}"
DEFAULT_CODEX_WORKSPACE_PATH="/workspace"
DEFAULT_BOT_LOG_FILE="${DEFAULT_CODEX_WORKSPACE_PATH}/logs/bot.log"
DEFAULT_GIT_USER_NAME="$(git config --global user.name || true)"
DEFAULT_GIT_USER_EMAIL="$(git config --global user.email || true)"

IMAGE_NAME="$(prompt_optional "IMAGE_NAME" "${DEFAULT_IMAGE_NAME}")"
CONTAINER_NAME="$(prompt_optional "CONTAINER_NAME" "${DEFAULT_CONTAINER_NAME}")"
CODEX_WORKSPACE_PATH="$(prompt_optional "CODEX_WORKSPACE_PATH" "${DEFAULT_CODEX_WORKSPACE_PATH}")"
BOT_LOG_FILE="$(prompt_optional "BOT_LOG_FILE" "${DEFAULT_BOT_LOG_FILE}")"

CODEX_SESSION_ID="$(prompt_optional_blank "CODEX_SESSION_ID")"
CODEX_TIMEOUT_SECONDS="$(prompt_optional_blank "CODEX_TIMEOUT_SECONDS")"
GH_TOKEN="$(prompt_optional_blank "GH_TOKEN")"
GIT_USER_NAME="$(prompt_optional "GIT_USER_NAME" "${DEFAULT_GIT_USER_NAME}")"
GIT_USER_EMAIL="$(prompt_optional "GIT_USER_EMAIL" "${DEFAULT_GIT_USER_EMAIL}")"

if [[ ! -f "${HOME}/.codex/auth.json" ]]; then
  echo "Error: Missing ${HOME}/.codex/auth.json"
  exit 1
fi
if [[ ! -d "${HOME}/.codex/sessions" ]]; then
  echo "Error: Missing ${HOME}/.codex/sessions"
  exit 1
fi

mkdir -p "${TARGET_REPO}/logs"

echo
echo "[1/3] Building image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" .

echo "[2/3] Removing previous container (if any): ${CONTAINER_NAME}"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "[3/3] Starting container with mounted workspace: ${TARGET_REPO}"
docker_args=(
  run -d
  --name "${CONTAINER_NAME}"
  -e "SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}"
  -e "SLACK_APP_TOKEN=${SLACK_APP_TOKEN}"
  -e "SLACK_ALLOWED_CHANNELS=${SLACK_ALLOWED_CHANNELS}"
  -e "CODEX_WORKSPACE_PATH=${CODEX_WORKSPACE_PATH}"
  -e "BOT_LOG_FILE=${BOT_LOG_FILE}"
  -v "${TARGET_REPO}:${CODEX_WORKSPACE_PATH}"
  -v "${TARGET_REPO}/logs:${CODEX_WORKSPACE_PATH}/logs"
  -v "${HOME}/.codex/auth.json:/run/secrets/codex_auth.json:ro"
  -v "${HOME}/.codex/sessions:/run/secrets/codex_sessions:ro"
)

if [[ -n "${CODEX_SESSION_ID}" ]]; then
  docker_args+=(-e "CODEX_SESSION_ID=${CODEX_SESSION_ID}")
fi
if [[ -n "${CODEX_TIMEOUT_SECONDS}" ]]; then
  docker_args+=(-e "CODEX_TIMEOUT_SECONDS=${CODEX_TIMEOUT_SECONDS}")
fi
if [[ -n "${GH_TOKEN}" ]]; then
  docker_args+=(-e "GH_TOKEN=${GH_TOKEN}" -e "GITHUB_TOKEN=${GH_TOKEN}")
fi
if [[ -n "${GIT_USER_NAME}" ]]; then
  docker_args+=(-e "GIT_USER_NAME=${GIT_USER_NAME}")
fi
if [[ -n "${GIT_USER_EMAIL}" ]]; then
  docker_args+=(-e "GIT_USER_EMAIL=${GIT_USER_EMAIL}")
fi

docker_args+=("${IMAGE_NAME}")
docker "${docker_args[@]}"

echo
echo "Container started: ${CONTAINER_NAME}"
echo "Follow logs:"
echo "  docker logs -f ${CONTAINER_NAME}"
