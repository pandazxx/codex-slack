#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_PATH="${CODEX_WORKSPACE_PATH:-/workspace}"
PROJECT_CODEX_HOME="${WORKSPACE_PATH}/.codex"
DEFAULT_CODEX_HOME="/home/appuser/.codex"

if [[ -n "${CODEX_HOME:-}" ]]; then
  CODEX_HOME_PATH="${CODEX_HOME}"
elif [[ -d "${PROJECT_CODEX_HOME}" ]]; then
  CODEX_HOME_PATH="${PROJECT_CODEX_HOME}"
else
  CODEX_HOME_PATH="${DEFAULT_CODEX_HOME}"
fi

export CODEX_HOME="${CODEX_HOME_PATH}"
mkdir -p "${CODEX_HOME_PATH}"

if [[ -f "/run/secrets/codex_auth.json" && ! -f "${CODEX_HOME_PATH}/auth.json" ]]; then
  cp "/run/secrets/codex_auth.json" "${CODEX_HOME_PATH}/auth.json"
  chmod 600 "${CODEX_HOME_PATH}/auth.json"
fi

if [[ -d "/run/secrets/codex_sessions" ]] && (
  [[ ! -d "${CODEX_HOME_PATH}/sessions" ]] ||
  [[ -z "$(ls -A "${CODEX_HOME_PATH}/sessions" 2>/dev/null)" ]]
); then
  mkdir -p "${CODEX_HOME_PATH}/sessions"
  cp -a /run/secrets/codex_sessions/. "${CODEX_HOME_PATH}/sessions/"
fi

# Copy global claude config into ~/.claude/ so claude has a writable config dir.
# Prefer the host-mounted path (injected by master when MASTER_PROJECT_DIR is set),
# fall back to the baked-in image path (always present when using the standard image).
# Pointing CLAUDE_CONFIG_DIR at a read-only mount causes claude to hang on first write.
CLAUDE_HOME="${HOME:-/workspace/home}/.claude"
if [[ -d "/run/secrets/master_claude_config" ]]; then
  mkdir -p "${CLAUDE_HOME}"
  cp -a /run/secrets/master_claude_config/. "${CLAUDE_HOME}/"
elif [[ -d "/opt/codex-slack/config/claude-global" ]]; then
  mkdir -p "${CLAUDE_HOME}"
  cp -a /opt/codex-slack/config/claude-global/. "${CLAUDE_HOME}/"
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

MODE="${CODEX_CONTAINER_MODE:-bot}"

if [[ "${MODE}" == "agent-worker" ]]; then
  if [[ "$#" -eq 0 ]] || [[ "$*" == "python -m src.bot.main" ]]; then
    exec python -m src.agent.main
  fi
  exec "$@"
fi

exec "$@" "${SESSION_ARGS[@]}"
