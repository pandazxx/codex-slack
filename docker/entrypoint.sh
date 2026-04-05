#!/usr/bin/env bash
set -euo pipefail

entrypoint_log() {
  echo "agent.entrypoint $*" >&2
}

count_entries() {
  local path="$1"
  if [[ ! -d "$path" ]]; then
    echo 0
    return
  fi
  find "$path" -mindepth 1 | wc -l | tr -d ' '
}

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
entrypoint_log "startup workspace_path=${WORKSPACE_PATH} project_codex_home=${PROJECT_CODEX_HOME} default_codex_home=${DEFAULT_CODEX_HOME} selected_codex_home=${CODEX_HOME_PATH} home=${HOME:--} xdg_config_home=${XDG_CONFIG_HOME:--}"

# Copy global codex config into CODEX_HOME so codex has a writable user-scope config dir.
# Prefer the host-mounted path, fall back to the baked-in image path.
CODEX_CONFIG_SOURCE="-"
if [[ -d "/run/secrets/master_codex_config" ]]; then
  CODEX_CONFIG_SOURCE="/run/secrets/master_codex_config"
  entrypoint_log "codex_config source=mounted path=${CODEX_CONFIG_SOURCE} entries=$(count_entries "${CODEX_CONFIG_SOURCE}") instructions_md=$([[ -f "${CODEX_CONFIG_SOURCE}/instructions.md" ]] && echo true || echo false) config_toml=$([[ -f "${CODEX_CONFIG_SOURCE}/config.toml" ]] && echo true || echo false)"
  cp -af /run/secrets/master_codex_config/. "${CODEX_HOME_PATH}/"
elif [[ -d "/opt/codex-slack/config/codex-global" ]]; then
  CODEX_CONFIG_SOURCE="/opt/codex-slack/config/codex-global"
  entrypoint_log "codex_config source=baked_in path=${CODEX_CONFIG_SOURCE} entries=$(count_entries "${CODEX_CONFIG_SOURCE}") instructions_md=$([[ -f "${CODEX_CONFIG_SOURCE}/instructions.md" ]] && echo true || echo false) config_toml=$([[ -f "${CODEX_CONFIG_SOURCE}/config.toml" ]] && echo true || echo false)"
  cp -af /opt/codex-slack/config/codex-global/. "${CODEX_HOME_PATH}/"
else
  entrypoint_log "codex_config source=none mounted_exists=$([[ -d "/run/secrets/master_codex_config" ]] && echo true || echo false) baked_in_exists=$([[ -d "/opt/codex-slack/config/codex-global" ]] && echo true || echo false)"
fi
entrypoint_log "codex_config_result target=${CODEX_HOME_PATH} entries=$(count_entries "${CODEX_HOME_PATH}") instructions_md=$([[ -f "${CODEX_HOME_PATH}/instructions.md" ]] && echo true || echo false) config_toml=$([[ -f "${CODEX_HOME_PATH}/config.toml" ]] && echo true || echo false) auth_json=$([[ -f "${CODEX_HOME_PATH}/auth.json" ]] && echo true || echo false) source=${CODEX_CONFIG_SOURCE}"

if [[ -f "/run/secrets/codex_auth.json" && ! -f "${CODEX_HOME_PATH}/auth.json" ]]; then
  entrypoint_log "codex_auth action=copy source=/run/secrets/codex_auth.json target=${CODEX_HOME_PATH}/auth.json"
  cp "/run/secrets/codex_auth.json" "${CODEX_HOME_PATH}/auth.json"
  chmod 600 "${CODEX_HOME_PATH}/auth.json"
else
  entrypoint_log "codex_auth action=skip source_exists=$([[ -f "/run/secrets/codex_auth.json" ]] && echo true || echo false) target_exists=$([[ -f "${CODEX_HOME_PATH}/auth.json" ]] && echo true || echo false)"
fi

if [[ -d "/run/secrets/codex_sessions" ]] && (
  [[ ! -d "${CODEX_HOME_PATH}/sessions" ]] ||
  [[ -z "$(ls -A "${CODEX_HOME_PATH}/sessions" 2>/dev/null)" ]]
); then
  entrypoint_log "codex_sessions action=copy source=/run/secrets/codex_sessions target=${CODEX_HOME_PATH}/sessions source_entries=$(count_entries /run/secrets/codex_sessions)"
  mkdir -p "${CODEX_HOME_PATH}/sessions"
  cp -a /run/secrets/codex_sessions/. "${CODEX_HOME_PATH}/sessions/"
else
  entrypoint_log "codex_sessions action=skip source_exists=$([[ -d "/run/secrets/codex_sessions" ]] && echo true || echo false) target_exists=$([[ -d "${CODEX_HOME_PATH}/sessions" ]] && echo true || echo false) target_entries=$(count_entries "${CODEX_HOME_PATH}/sessions")"
fi
entrypoint_log "codex_home_result target=${CODEX_HOME_PATH} entries=$(count_entries "${CODEX_HOME_PATH}") instructions_md=$([[ -f "${CODEX_HOME_PATH}/instructions.md" ]] && echo true || echo false) config_toml=$([[ -f "${CODEX_HOME_PATH}/config.toml" ]] && echo true || echo false) auth_json=$([[ -f "${CODEX_HOME_PATH}/auth.json" ]] && echo true || echo false) sessions_dir=$([[ -d "${CODEX_HOME_PATH}/sessions" ]] && echo true || echo false)"

# Copy global claude config into ~/.claude/ so claude has a writable config dir.
# Prefer the host-mounted path (injected by master when MASTER_PROJECT_DIR is set),
# fall back to the baked-in image path (always present when using the standard image).
# Pointing CLAUDE_CONFIG_DIR at a read-only mount causes claude to hang on first write.
CLAUDE_HOME="${HOME:-/workspace/home}/.claude"
CLAUDE_CONFIG_SOURCE="-"
if [[ -d "/run/secrets/master_claude_config" ]]; then
  mkdir -p "${CLAUDE_HOME}"
  CLAUDE_CONFIG_SOURCE="/run/secrets/master_claude_config"
  entrypoint_log "claude_config source=mounted path=${CLAUDE_CONFIG_SOURCE} entries=$(count_entries "${CLAUDE_CONFIG_SOURCE}") claude_md=$([[ -f "${CLAUDE_CONFIG_SOURCE}/CLAUDE.md" ]] && echo true || echo false) settings_json=$([[ -f "${CLAUDE_CONFIG_SOURCE}/settings.json" ]] && echo true || echo false)"
  cp -af /run/secrets/master_claude_config/. "${CLAUDE_HOME}/"
elif [[ -d "/opt/codex-slack/config/claude-global" ]]; then
  mkdir -p "${CLAUDE_HOME}"
  CLAUDE_CONFIG_SOURCE="/opt/codex-slack/config/claude-global"
  entrypoint_log "claude_config source=baked_in path=${CLAUDE_CONFIG_SOURCE} entries=$(count_entries "${CLAUDE_CONFIG_SOURCE}") claude_md=$([[ -f "${CLAUDE_CONFIG_SOURCE}/CLAUDE.md" ]] && echo true || echo false) settings_json=$([[ -f "${CLAUDE_CONFIG_SOURCE}/settings.json" ]] && echo true || echo false)"
  cp -af /opt/codex-slack/config/claude-global/. "${CLAUDE_HOME}/"
else
  entrypoint_log "claude_config source=none mounted_exists=$([[ -d "/run/secrets/master_claude_config" ]] && echo true || echo false) baked_in_exists=$([[ -d "/opt/codex-slack/config/claude-global" ]] && echo true || echo false)"
fi
entrypoint_log "claude_config_result target=${CLAUDE_HOME} entries=$(count_entries "${CLAUDE_HOME}") claude_md=$([[ -f "${CLAUDE_HOME}/CLAUDE.md" ]] && echo true || echo false) settings_json=$([[ -f "${CLAUDE_HOME}/settings.json" ]] && echo true || echo false) source=${CLAUDE_CONFIG_SOURCE}"

if [[ -n "${GIT_USER_NAME:-}" ]]; then
  entrypoint_log "git_config key=user.name action=set"
  git config --global user.name "${GIT_USER_NAME}"
fi

if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
  entrypoint_log "git_config key=user.email action=set"
  git config --global user.email "${GIT_USER_EMAIL}"
fi

SESSION_ARGS=()
if [[ -n "${CODEX_SESSION_ID:-}" ]]; then
  SESSION_ARGS+=("--session-id" "${CODEX_SESSION_ID}")
fi

MODE="${CODEX_CONTAINER_MODE:-bot}"
entrypoint_log "launch mode=${MODE} command=$* session_id=${CODEX_SESSION_ID:--}"

if [[ "${MODE}" == "agent-worker" ]]; then
  if [[ "$#" -eq 0 ]] || [[ "$*" == "python -m src.bot.main" ]]; then
    entrypoint_log "launch_exec target=python -m src.agent.main"
    exec python -m src.agent.main
  fi
  entrypoint_log "launch_exec target=$*"
  exec "$@"
fi

entrypoint_log "launch_exec target=$* session_args_count=${#SESSION_ARGS[@]}"
exec "$@" "${SESSION_ARGS[@]}"
