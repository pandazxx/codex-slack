#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME_PATH="${CODEX_HOME:-/tmp/.codex}"
mkdir -p "${CODEX_HOME_PATH}"

SESSION_ARGS=()
if [[ -n "${CODEX_SESSION_ID:-}" ]]; then
  SESSION_ARGS+=("--session-id" "${CODEX_SESSION_ID}")
fi

exec "$@" "${SESSION_ARGS[@]}"
