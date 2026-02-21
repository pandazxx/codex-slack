#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME_PATH="${CODEX_HOME:-/home/appuser/.codex}"
mkdir -p "${CODEX_HOME_PATH}"

if [[ -f "/run/secrets/codex_auth.json" ]]; then
  cp "/run/secrets/codex_auth.json" "${CODEX_HOME_PATH}/auth.json"
  chmod 600 "${CODEX_HOME_PATH}/auth.json"
fi

if [[ -n "${GIT_USER_NAME:-}" ]]; then
  git config --global user.name "${GIT_USER_NAME}"
fi

if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
  git config --global user.email "${GIT_USER_EMAIL}"
fi

SESSION_ARGS=()
if [[ -n "${CODEX_SESSION_ID:-}" ]]; then
  SESSION_ARGS+=("--session-id" "${CODEX_SESSION_ID}")
fi

exec "$@" "${SESSION_ARGS[@]}"
